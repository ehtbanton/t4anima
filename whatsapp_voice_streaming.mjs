/**
 * WhatsApp Voice Call Server with Real-time Audio Streaming
 *
 * Uses a FIFO + background ffmpeg process to enable mid-call audio.
 * The trick: we create a raw PCM FIFO that ffmpeg reads from continuously,
 * and we write TTS audio to it when we want the agent to speak.
 *
 * API:
 *   POST /call { phoneNumber, patientId } - Initiate call
 *   POST /hangup { callId } - End call
 *   GET /status - Get active calls
 */

import { VoipClient } from "baileys-caller";
import { createServer } from "http";
import { spawn, execSync } from "child_process";
import { writeFileSync, unlinkSync, existsSync, openSync, writeSync, closeSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const PORT = 3001;
const AUTH_DIR = "./whatsapp_session";

// Load environment
import { config } from "dotenv";
config({ path: join(process.env.HOME, ".env") });

const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const ELEVENLABS_API_KEY = process.env.ELEVENLABS_API_KEY;

// Active calls tracking
const activeCalls = new Map();
let voipClient = null;

// Patient data
let patients = [];
try {
    const fs = await import("fs");
    const data = fs.readFileSync("data/patients_50k.json", "utf-8");
    patients = JSON.parse(data);
    console.log(`Loaded ${patients.length} patients`);
} catch (e) {
    console.log("No patient data loaded");
}
const patientsById = new Map(patients.map(p => [p.id, p]));

// System prompt
function getSystemPrompt(patient) {
    if (!patient) {
        return `You are a friendly NHS StrokeGuard patient support assistant.
Be warm, empathetic, and reassuring. Speak in plain English.
If they mention chest pain, sudden weakness, speech problems, or severe headache, tell them to call 999 immediately.
Keep responses brief - 1-2 sentences max for natural conversation.`;
    }
    const conditions = patient.conditions?.map(c => c.term).join(", ") || "None";
    const riskPct = ((patient.stroke_risk || 0) * 100).toFixed(1);
    return `You are a friendly NHS StrokeGuard patient support assistant calling patient ${patient.id}.

PATIENT PROFILE:
- Age: ${patient.age}, Sex: ${patient.sex}
- Blood Pressure: ${patient.sbp}/${patient.dbp} mmHg
- Stroke Risk: ${riskPct}%
- Conditions: ${conditions}
- Smoker: ${patient.smoking ? "Yes" : "No"}

GUIDELINES:
- Be warm, empathetic, reassuring
- Use plain English, no medical jargon
- Keep responses to 1-2 sentences
- EMERGENCY: chest pain, sudden weakness, speech problems, severe headache - tell them to CALL 999 IMMEDIATELY`;
}

/**
 * AudioStreamer - manages a FIFO that ffmpeg reads from
 * We write PCM audio to the FIFO when we want to speak
 */
class AudioStreamer {
    constructor(callId) {
        this.callId = callId;
        this.fifoPath = join(tmpdir(), `wa_audio_${callId}.pcm`);
        this.isActive = false;
        this.audioQueue = [];
        this.fifoFd = null;
        this.silenceChunk = Buffer.alloc(640, 0); // 20ms of silence at 16kHz 16-bit mono
        this.writeInterval = null;
    }

    async start() {
        // Create FIFO
        if (existsSync(this.fifoPath)) unlinkSync(this.fifoPath);
        execSync(`mkfifo "${this.fifoPath}"`);
        this.isActive = true;

        // Start the write loop in background
        // This needs to happen AFTER the call starts reading from the FIFO
        return this.fifoPath;
    }

    // Call this after the call connects
    startWriting() {
        if (this.writeInterval) return;

        console.log(`[AudioStreamer ${this.callId}] Starting write loop`);

        // Open FIFO for writing (this blocks until reader connects)
        try {
            this.fifoFd = openSync(this.fifoPath, "w");
        } catch (e) {
            console.error(`[AudioStreamer] Failed to open FIFO:`, e);
            return;
        }

        // Write audio every 20ms
        this.writeInterval = setInterval(() => {
            if (!this.isActive || !this.fifoFd) {
                this.stopWriting();
                return;
            }

            try {
                if (this.audioQueue.length > 0) {
                    const chunk = this.audioQueue.shift();
                    writeSync(this.fifoFd, chunk);
                } else {
                    // Write silence
                    writeSync(this.fifoFd, this.silenceChunk);
                }
            } catch (e) {
                // FIFO closed
                this.stopWriting();
            }
        }, 20);
    }

    stopWriting() {
        if (this.writeInterval) {
            clearInterval(this.writeInterval);
            this.writeInterval = null;
        }
        if (this.fifoFd) {
            try { closeSync(this.fifoFd); } catch {}
            this.fifoFd = null;
        }
    }

    // Queue PCM audio to be played
    // pcmBuffer should be 16-bit signed integer, 16kHz, mono
    queuePcm(pcmBuffer) {
        if (!pcmBuffer || pcmBuffer.length === 0) return;

        // Split into 20ms chunks (640 bytes = 320 samples * 2 bytes)
        const chunkSize = 640;
        let queued = 0;
        for (let i = 0; i < pcmBuffer.length; i += chunkSize) {
            const chunk = Buffer.from(pcmBuffer.subarray(i, Math.min(i + chunkSize, pcmBuffer.length)));
            this.audioQueue.push(chunk);
            queued++;
        }
        console.log(`[AudioStreamer ${this.callId}] Queued ${queued} chunks (${(pcmBuffer.length / 32000).toFixed(2)}s)`);
    }

    stop() {
        this.isActive = false;
        this.stopWriting();
        if (existsSync(this.fifoPath)) {
            try { unlinkSync(this.fifoPath); } catch {}
        }
    }
}

// Text-to-Speech - returns 16-bit 16kHz mono PCM
async function textToSpeechPcm(text) {
    if (!ELEVENLABS_API_KEY) return null;

    const response = await fetch("https://api.elevenlabs.io/v1/text-to-speech/TxGEqnHWrfWFTfGW9XjX", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY,
        },
        body: JSON.stringify({
            text,
            model_id: "eleven_turbo_v2_5",
            voice_settings: { stability: 0.5, similarity_boost: 0.75 },
            output_format: "pcm_16000", // 16kHz 16-bit mono PCM
        }),
    });

    if (!response.ok) {
        console.error("TTS error:", await response.text());
        return null;
    }

    return Buffer.from(await response.arrayBuffer());
}

// Transcribe audio with Whisper
async function transcribeAudio(wavBuffer) {
    if (!OPENAI_API_KEY) return null;

    try {
        const blob = new Blob([wavBuffer], { type: "audio/wav" });
        const formData = new FormData();
        formData.append("file", blob, "audio.wav");
        formData.append("model", "whisper-1");
        formData.append("language", "en");

        const response = await fetch("https://api.openai.com/v1/audio/transcriptions", {
            method: "POST",
            headers: { "Authorization": `Bearer ${OPENAI_API_KEY}` },
            body: formData
        });

        if (!response.ok) {
            console.error("Whisper error:", await response.text());
            return null;
        }

        return (await response.json()).text;
    } catch (e) {
        console.error("Transcription error:", e);
        return null;
    }
}

// Get AI response
async function getAIResponse(messages) {
    if (!OPENAI_API_KEY) return "I'm sorry, I'm having trouble connecting.";

    const response = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${OPENAI_API_KEY}`,
        },
        body: JSON.stringify({
            model: "gpt-4o",
            messages,
            max_tokens: 150,
            temperature: 0.7,
        }),
    });

    if (!response.ok) {
        console.error("OpenAI error:", await response.text());
        return "I'm sorry, could you repeat that?";
    }

    return (await response.json()).choices[0]?.message?.content || "Could you repeat that?";
}

// Create WAV header
function createWavHeader(dataLength) {
    const header = Buffer.alloc(44);
    header.write('RIFF', 0);
    header.writeUInt32LE(36 + dataLength, 4);
    header.write('WAVE', 8);
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20);    // PCM
    header.writeUInt16LE(1, 22);    // mono
    header.writeUInt32LE(16000, 24); // sample rate
    header.writeUInt32LE(32000, 28); // byte rate
    header.writeUInt16LE(2, 32);    // block align
    header.writeUInt16LE(16, 34);   // bits per sample
    header.write('data', 36);
    header.writeUInt32LE(dataLength, 40);
    return header;
}

// Initialize WhatsApp
async function initWhatsApp() {
    if (voipClient) return voipClient;
    console.log("Initializing WhatsApp VoIP client...");
    voipClient = new VoipClient({ authDir: AUTH_DIR });
    await voipClient.connect();
    console.log("✅ WhatsApp connected");
    return voipClient;
}

// Make a call with streaming audio
async function makeCall(phoneNumber, patientId) {
    const client = await initWhatsApp();
    const patient = patientsById.get(patientId);
    const cleanNumber = phoneNumber.replace(/[^0-9]/g, "");
    const callId = `call_${Date.now()}`;

    console.log(`📞 Calling +${cleanNumber} (Patient: ${patientId || "unknown"}) [${callId}]`);

    const systemPrompt = getSystemPrompt(patient);
    const greeting = patient
        ? `Hello! This is the StrokeGuard patient support line. Am I speaking with patient ${patientId}?`
        : "Hello! This is the StrokeGuard patient support line. How can I help you today?";

    // Create audio streamer
    const streamer = new AudioStreamer(callId);
    const fifoPath = await streamer.start();

    // Generate greeting audio and queue it
    const greetingPcm = await textToSpeechPcm(greeting);
    if (greetingPcm) {
        streamer.queuePcm(greetingPcm);
    }

    // Start the call with our FIFO as the audio source
    // The FIFO will be read as raw PCM by ffmpeg
    const call = await client.call(cleanNumber, {
        audioSource: fifoPath,
        durationMs: 300000,
    });

    const callState = {
        id: callId,
        phoneNumber: cleanNumber,
        patientId,
        patient,
        systemPrompt,
        messages: [{ role: "system", content: systemPrompt }],
        status: "ringing",
        startTime: Date.now(),
        call,
        streamer,
    };

    activeCalls.set(callId, callState);

    call.on("ringing", () => {
        console.log(`📳 ${callId}: Ringing...`);
        callState.status = "ringing";
    });

    call.on("connected", () => {
        console.log(`✅ ${callId}: Connected!`);
        callState.status = "connected";
        callState.messages.push({ role: "assistant", content: greeting });

        // Start writing to FIFO now that call is connected
        streamer.startWriting();
    });

    // Audio processing
    let audioChunks = [];
    let silenceStart = null;
    let isProcessing = false;
    const SILENCE_THRESHOLD = 0.01;
    const SILENCE_DURATION = 1500;

    call.on("audio", async (pcm) => {
        if (isProcessing) return;

        let sum = 0;
        for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
        const rms = Math.sqrt(sum / pcm.length);

        if (rms < SILENCE_THRESHOLD) {
            if (!silenceStart) silenceStart = Date.now();

            if (audioChunks.length > 0 && Date.now() - silenceStart > SILENCE_DURATION) {
                isProcessing = true;

                // Convert to 16-bit PCM
                const totalLength = audioChunks.reduce((s, c) => s + c.length, 0);
                const int16Data = new Int16Array(totalLength);
                let offset = 0;
                for (const chunk of audioChunks) {
                    for (let i = 0; i < chunk.length; i++) {
                        int16Data[offset++] = Math.max(-32768, Math.min(32767, Math.floor(chunk[i] * 32767)));
                    }
                }
                const pcmBuffer = Buffer.from(int16Data.buffer);

                audioChunks = [];
                silenceStart = null;

                try {
                    // Create WAV for Whisper
                    const wavBuffer = Buffer.concat([createWavHeader(pcmBuffer.length), pcmBuffer]);

                    const transcript = await transcribeAudio(wavBuffer);
                    if (transcript && transcript.trim()) {
                        console.log(`🎤 Patient: ${transcript}`);
                        callState.messages.push({ role: "user", content: transcript });

                        const response = await getAIResponse(callState.messages);
                        console.log(`🤖 Agent: ${response}`);
                        callState.messages.push({ role: "assistant", content: response });

                        // Generate TTS and queue it
                        const responsePcm = await textToSpeechPcm(response);
                        if (responsePcm) {
                            streamer.queuePcm(responsePcm);
                            console.log(`🔊 Queued audio response`);
                        }
                    }
                } catch (e) {
                    console.error("Audio processing error:", e);
                }

                isProcessing = false;
            }
        } else {
            silenceStart = null;
            audioChunks.push(new Float32Array(pcm));
        }
    });

    call.on("ended", (reason) => {
        console.log(`📴 ${callId}: Ended - ${reason}`);
        callState.status = "ended";
        callState.endReason = reason;
        streamer.stop();
        setTimeout(() => activeCalls.delete(callId), 60000);
    });

    return { callId, status: "initiated" };
}

// HTTP Server
const server = createServer(async (req, res) => {
    const url = new URL(req.url, `http://localhost:${PORT}`);

    res.setHeader("Access-Control-Allow-Origin", "*");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");

    if (req.method === "OPTIONS") { res.writeHead(200); res.end(); return; }

    let body = "";
    if (req.method === "POST") {
        for await (const chunk of req) body += chunk;
    }

    const json = (data, status = 200) => {
        res.writeHead(status, { "Content-Type": "application/json" });
        res.end(JSON.stringify(data));
    };

    try {
        if (url.pathname === "/health") {
            json({ status: "ok", whatsapp: !!voipClient });
        }
        else if (url.pathname === "/call" && req.method === "POST") {
            const { phoneNumber, patientId } = JSON.parse(body);
            if (!phoneNumber) { json({ error: "phoneNumber required" }, 400); return; }
            json(await makeCall(phoneNumber, patientId));
        }
        else if (url.pathname === "/hangup" && req.method === "POST") {
            const { callId } = JSON.parse(body);
            const callState = activeCalls.get(callId);
            if (callState?.call) {
                callState.call.end();
                json({ status: "hung_up" });
            } else {
                json({ error: "Call not found" }, 404);
            }
        }
        else if (url.pathname === "/status") {
            const calls = Array.from(activeCalls.values()).map(c => ({
                id: c.id,
                phoneNumber: c.phoneNumber,
                patientId: c.patientId,
                status: c.status,
                duration: Date.now() - c.startTime,
                messages: c.messages.filter(m => m.role !== 'system').map(m => ({ role: m.role, content: m.content }))
            }));
            json({ calls, whatsappConnected: !!voipClient });
        }
        else { json({ error: "Not found" }, 404); }
    } catch (err) {
        console.error("Error:", err);
        json({ error: err.message }, 500);
    }
});

server.listen(PORT, () => {
    console.log(`\n${"═".repeat(50)}`);
    console.log("  STROKEGUARD WHATSAPP VOICE SERVER (Streaming)");
    console.log(`${"═".repeat(50)}`);
    console.log(`\n🚀 Server running on http://localhost:${PORT}`);
    console.log("\nEndpoints:");
    console.log("  POST /call    - { phoneNumber, patientId? }");
    console.log("  POST /hangup  - { callId }");
    console.log("  GET  /status  - List active calls\n");
    initWhatsApp().catch(console.error);
});
