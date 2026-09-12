/**
 * WhatsApp Voice Call Server
 * Bridges WhatsApp calls with AI voice agent using FIFO for real-time audio
 *
 * API:
 *   POST /call { phoneNumber, patientId } - Initiate call
 *   POST /hangup { callId } - End call
 *   GET /status - Get active calls
 */

import { VoipClient } from "baileys-caller";
import { createServer } from "http";
import { spawn, execSync } from "child_process";
import { writeFileSync, unlinkSync, existsSync, createWriteStream, openSync, closeSync, writeSync } from "fs";
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

// Patient data (loaded from JSON)
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

// System prompt for the AI agent
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
- Keep responses to 1-2 sentences for natural conversation
- If risk >15%, suggest scheduling a follow-up
- EMERGENCY: If they mention chest pain, sudden weakness, speech problems, severe headache - tell them to CALL 999 IMMEDIATELY

Start by greeting them and asking how they're feeling today.`;
}

// Text-to-Speech using ElevenLabs - returns raw audio buffer
async function textToSpeechBuffer(text) {
    if (!ELEVENLABS_API_KEY) {
        console.log("No ElevenLabs key, skipping TTS");
        return null;
    }

    const response = await fetch("https://api.elevenlabs.io/v1/text-to-speech/TxGEqnHWrfWFTfGW9XjX", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY,
        },
        body: JSON.stringify({
            text,
            model_id: "eleven_turbo_v2_5",
            voice_settings: {
                stability: 0.5,
                similarity_boost: 0.75,
            },
            output_format: "pcm_16000", // 16kHz 16-bit mono PCM for direct FIFO writing
        }),
    });

    if (!response.ok) {
        console.error("TTS error:", await response.text());
        return null;
    }

    return Buffer.from(await response.arrayBuffer());
}

// Text-to-Speech to file (for initial greeting)
async function textToSpeechFile(text) {
    if (!ELEVENLABS_API_KEY) {
        console.log("No ElevenLabs key, skipping TTS");
        return null;
    }

    const response = await fetch("https://api.elevenlabs.io/v1/text-to-speech/TxGEqnHWrfWFTfGW9XjX", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "xi-api-key": ELEVENLABS_API_KEY,
        },
        body: JSON.stringify({
            text,
            model_id: "eleven_turbo_v2_5",
            voice_settings: {
                stability: 0.5,
                similarity_boost: 0.75,
            },
        }),
    });

    if (!response.ok) {
        console.error("TTS error:", await response.text());
        return null;
    }

    const audioBuffer = await response.arrayBuffer();
    const tempFile = join(tmpdir(), `tts_${Date.now()}.mp3`);
    writeFileSync(tempFile, Buffer.from(audioBuffer));
    return tempFile;
}

// Create WAV header for PCM data
function createWavHeader(dataLength, sampleRate, channels, bitsPerSample) {
    const header = Buffer.alloc(44);
    const byteRate = sampleRate * channels * bitsPerSample / 8;
    const blockAlign = channels * bitsPerSample / 8;

    header.write('RIFF', 0);
    header.writeUInt32LE(36 + dataLength, 4);
    header.write('WAVE', 8);
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20);
    header.writeUInt16LE(channels, 22);
    header.writeUInt32LE(sampleRate, 24);
    header.writeUInt32LE(byteRate, 28);
    header.writeUInt16LE(blockAlign, 32);
    header.writeUInt16LE(bitsPerSample, 34);
    header.write('data', 36);
    header.writeUInt32LE(dataLength, 40);

    return header;
}

// Transcribe audio using OpenAI Whisper
async function transcribeAudio(wavFile) {
    if (!OPENAI_API_KEY) return null;

    const fs = await import("fs");

    try {
        const fileBuffer = fs.readFileSync(wavFile);
        const blob = new Blob([fileBuffer], { type: "audio/wav" });

        const formData = new FormData();
        formData.append("file", blob, "audio.wav");
        formData.append("model", "whisper-1");
        formData.append("language", "en");

        const response = await fetch("https://api.openai.com/v1/audio/transcriptions", {
            method: "POST",
            headers: {
                "Authorization": `Bearer ${OPENAI_API_KEY}`
            },
            body: formData
        });

        if (!response.ok) {
            console.error("Whisper error:", await response.text());
            return null;
        }

        const data = await response.json();
        return data.text;
    } catch (e) {
        console.error("Transcription error:", e);
        return null;
    }
}

// Chat completion using OpenAI
async function getAIResponse(messages) {
    if (!OPENAI_API_KEY) {
        return "I'm sorry, I'm having trouble connecting. Please try again.";
    }

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

    const data = await response.json();
    return data.choices[0]?.message?.content || "Could you repeat that?";
}

// Create a FIFO for streaming audio
function createFifo(callId) {
    const fifoPath = join(tmpdir(), `wa_audio_${callId}.fifo`);
    try {
        // Remove if exists
        if (existsSync(fifoPath)) {
            unlinkSync(fifoPath);
        }
        // Create FIFO
        execSync(`mkfifo "${fifoPath}"`);
        console.log(`Created FIFO: ${fifoPath}`);
        return fifoPath;
    } catch (e) {
        console.error("Failed to create FIFO:", e);
        return null;
    }
}

// Write audio to FIFO (non-blocking with sox conversion)
async function writeAudioToFifo(fifoPath, pcmBuffer) {
    if (!fifoPath || !pcmBuffer) return;

    // pcmBuffer is 16-bit 16kHz mono PCM from ElevenLabs
    // Need to convert to f32le (float32) for baileys-caller
    const tempPcm = join(tmpdir(), `temp_${Date.now()}.raw`);
    writeFileSync(tempPcm, pcmBuffer);

    return new Promise((resolve) => {
        // Use sox to convert 16-bit PCM to 32-bit float
        const sox = spawn("sox", [
            "-t", "raw", "-r", "16000", "-b", "16", "-c", "1", "-e", "signed-integer", tempPcm,
            "-t", "raw", "-r", "16000", "-b", "32", "-c", "1", "-e", "floating-point", fifoPath
        ]);

        sox.on("close", (code) => {
            if (existsSync(tempPcm)) unlinkSync(tempPcm);
            if (code !== 0) console.error(`sox exited with code ${code}`);
            resolve();
        });

        sox.on("error", (err) => {
            console.error("sox error:", err);
            if (existsSync(tempPcm)) unlinkSync(tempPcm);
            resolve();
        });
    });
}

// Initialize WhatsApp client
async function initWhatsApp() {
    if (voipClient) return voipClient;

    console.log("Initializing WhatsApp VoIP client...");
    voipClient = new VoipClient({ authDir: AUTH_DIR });
    await voipClient.connect();
    console.log("✅ WhatsApp connected");
    return voipClient;
}

// Make a call with real-time audio streaming
async function makeCall(phoneNumber, patientId) {
    const client = await initWhatsApp();
    const patient = patientsById.get(patientId);

    const cleanNumber = phoneNumber.replace(/[^0-9]/g, "");
    const callId = `call_${Date.now()}`;
    console.log(`📞 Calling +${cleanNumber} (Patient: ${patientId || "unknown"}) [${callId}]`);

    // Generate greeting
    const systemPrompt = getSystemPrompt(patient);
    const greeting = patient
        ? `Hello! This is the StrokeGuard patient support line. Am I speaking with the patient registered as ${patientId}?`
        : "Hello! This is the StrokeGuard patient support line. How can I help you today?";

    // Generate TTS for greeting as an MP3 file (for initial audioSource)
    const greetingAudio = await textToSpeechFile(greeting);

    const call = await client.call(cleanNumber, {
        audioSource: greetingAudio || "silence",
        durationMs: 300000, // 5 min max
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
        greetingAudio,
        responseQueue: [], // Queue TTS responses
        isPlayingResponse: false,
    };

    activeCalls.set(callId, callState);

    call.on("ringing", () => {
        console.log(`📳 ${callId}: Ringing...`);
        callState.status = "ringing";
    });

    call.on("connected", async () => {
        console.log(`✅ ${callId}: Connected!`);
        callState.status = "connected";

        // Add greeting to conversation history
        callState.messages.push({ role: "assistant", content: greeting });
    });

    // Audio processing state
    let audioChunks = [];
    let silenceStart = null;
    let isProcessing = false;
    const SILENCE_THRESHOLD = 0.01;
    const SILENCE_DURATION = 1500;

    call.on("audio", async (pcm) => {
        if (isProcessing || callState.isPlayingResponse) return;

        // Calculate RMS to detect silence
        let sum = 0;
        for (let i = 0; i < pcm.length; i++) {
            sum += pcm[i] * pcm[i];
        }
        const rms = Math.sqrt(sum / pcm.length);

        if (rms < SILENCE_THRESHOLD) {
            if (!silenceStart) silenceStart = Date.now();

            if (audioChunks.length > 0 && Date.now() - silenceStart > SILENCE_DURATION) {
                isProcessing = true;

                // Convert Float32Array chunks to Int16 PCM Buffer
                const totalLength = audioChunks.reduce((sum, chunk) => sum + chunk.length, 0);
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
                    // Save PCM to WAV file for Whisper
                    const wavFile = join(tmpdir(), `input_${Date.now()}.wav`);
                    const wavHeader = createWavHeader(pcmBuffer.length, 16000, 1, 16);
                    writeFileSync(wavFile, Buffer.concat([wavHeader, pcmBuffer]));

                    // Transcribe with Whisper
                    const transcript = await transcribeAudio(wavFile);
                    if (existsSync(wavFile)) unlinkSync(wavFile);

                    if (transcript && transcript.trim()) {
                        console.log(`🎤 Patient: ${transcript}`);
                        callState.messages.push({ role: "user", content: transcript });

                        // Get AI response
                        const response = await getAIResponse(callState.messages);
                        console.log(`🤖 Agent: ${response}`);
                        callState.messages.push({ role: "assistant", content: response });

                        // Generate TTS and queue it
                        // NOTE: baileys-caller doesn't support mid-call audio injection
                        // The response is logged but audio playback is not supported
                        // TODO: Fork baileys-caller to add call.sendAudio() method
                        console.log(`⚠️ Audio playback not supported - response logged only`);
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

        // Cleanup temp files
        if (greetingAudio && existsSync(greetingAudio)) {
            unlinkSync(greetingAudio);
        }

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

    if (req.method === "OPTIONS") {
        res.writeHead(200);
        res.end();
        return;
    }

    let body = "";
    if (req.method === "POST") {
        for await (const chunk of req) {
            body += chunk;
        }
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
            if (!phoneNumber) {
                json({ error: "phoneNumber required" }, 400);
                return;
            }
            const result = await makeCall(phoneNumber, patientId);
            json(result);
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
                messages: c.messages.filter(m => m.role !== 'system').map(m => ({
                    role: m.role,
                    content: m.content
                }))
            }));
            json({ calls, whatsappConnected: !!voipClient });
        }
        else if (url.pathname === "/transcript" && req.method === "GET") {
            const callId = url.searchParams.get("callId");
            const callState = activeCalls.get(callId);
            if (callState) {
                json({
                    messages: callState.messages.filter(m => m.role !== 'system')
                });
            } else {
                json({ error: "Call not found" }, 404);
            }
        }
        else {
            json({ error: "Not found" }, 404);
        }
    } catch (err) {
        console.error("Error:", err);
        json({ error: err.message }, 500);
    }
});

server.listen(PORT, () => {
    console.log(`\n${"═".repeat(50)}`);
    console.log("  STROKEGUARD WHATSAPP VOICE SERVER");
    console.log(`${"═".repeat(50)}`);
    console.log(`\n🚀 Server running on http://localhost:${PORT}`);
    console.log("\n⚠️  LIMITATION: baileys-caller does not support mid-call audio");
    console.log("   The greeting plays, but responses are logged only.");
    console.log("   To fix: fork baileys-caller and add call.sendAudio() method\n");
    console.log("Endpoints:");
    console.log("  POST /call    - { phoneNumber, patientId? }");
    console.log("  POST /hangup  - { callId }");
    console.log("  GET  /status  - List active calls");
    console.log("\nInitializing WhatsApp...\n");

    initWhatsApp().catch(console.error);
});
