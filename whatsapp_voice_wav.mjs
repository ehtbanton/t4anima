/**
 * WhatsApp Voice Call Server - WAV Concatenation Approach
 *
 * Strategy: Create a long WAV file with the greeting, then silence.
 * baileys-caller's ffmpeg reads this file continuously.
 * For responses, we can't inject mid-stream, but we log them.
 *
 * For true bidirectional audio, we'd need to fork baileys-caller or
 * use a different approach (Twilio, SIP gateway, etc.)
 */

import { VoipClient } from "baileys-caller";
import { createServer } from "http";
import { spawn } from "child_process";
import { writeFileSync, unlinkSync, existsSync, readFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

const PORT = 3001;
const AUTH_DIR = "./whatsapp_session";

import { config } from "dotenv";
config({ path: join(process.env.HOME, ".env") });

const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const ELEVENLABS_API_KEY = process.env.ELEVENLABS_API_KEY;

const activeCalls = new Map();
let voipClient = null;

// Patient data
let patients = [];
try {
    const fs = await import("fs");
    patients = JSON.parse(fs.readFileSync("data/patients_50k.json", "utf-8"));
    console.log(`Loaded ${patients.length} patients`);
} catch (e) {
    console.log("No patient data loaded");
}
const patientsById = new Map(patients.map(p => [p.id, p]));

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
- EMERGENCY: chest pain, sudden weakness, speech problems, severe headache - tell them to CALL 999`;
}

// Get MP3 from ElevenLabs
async function textToSpeechMp3(text) {
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
        }),
    });

    if (!response.ok) {
        console.error("TTS error:", await response.text());
        return null;
    }

    const buffer = Buffer.from(await response.arrayBuffer());
    const tempFile = join(tmpdir(), `tts_${Date.now()}.mp3`);
    writeFileSync(tempFile, buffer);
    return tempFile;
}

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

        if (!response.ok) return null;
        return (await response.json()).text;
    } catch (e) {
        console.error("Transcription error:", e);
        return null;
    }
}

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

    if (!response.ok) return "Could you repeat that?";
    return (await response.json()).choices[0]?.message?.content || "Could you repeat that?";
}

function createWavHeader(dataLength) {
    const header = Buffer.alloc(44);
    header.write('RIFF', 0);
    header.writeUInt32LE(36 + dataLength, 4);
    header.write('WAVE', 8);
    header.write('fmt ', 12);
    header.writeUInt32LE(16, 16);
    header.writeUInt16LE(1, 20);
    header.writeUInt16LE(1, 22);
    header.writeUInt32LE(16000, 24);
    header.writeUInt32LE(32000, 28);
    header.writeUInt16LE(2, 32);
    header.writeUInt16LE(16, 34);
    header.write('data', 36);
    header.writeUInt32LE(dataLength, 40);
    return header;
}

async function initWhatsApp() {
    if (voipClient) return voipClient;
    console.log("Initializing WhatsApp VoIP client...");
    voipClient = new VoipClient({ authDir: AUTH_DIR });
    await voipClient.connect();
    console.log("✅ WhatsApp connected");
    return voipClient;
}

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

    // Generate greeting audio
    const greetingFile = await textToSpeechMp3(greeting);
    console.log(`🎵 Greeting audio: ${greetingFile || "none"}`);

    const call = await client.call(cleanNumber, {
        audioSource: greetingFile || "silence",
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
        greetingFile,
        pendingResponses: [], // Responses that couldn't be played
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
    });

    // Audio processing
    let audioChunks = [];
    let silenceStart = null;
    let isProcessing = false;

    call.on("audio", async (pcm) => {
        if (isProcessing) return;

        let sum = 0;
        for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
        const rms = Math.sqrt(sum / pcm.length);

        if (rms < 0.01) {
            if (!silenceStart) silenceStart = Date.now();

            if (audioChunks.length > 0 && Date.now() - silenceStart > 1500) {
                isProcessing = true;

                const totalLength = audioChunks.reduce((s, c) => s + c.length, 0);
                const int16Data = new Int16Array(totalLength);
                let offset = 0;
                for (const chunk of audioChunks) {
                    for (let i = 0; i < chunk.length; i++) {
                        int16Data[offset++] = Math.max(-32768, Math.min(32767, Math.floor(chunk[i] * 32767)));
                    }
                }

                audioChunks = [];
                silenceStart = null;

                try {
                    const pcmBuffer = Buffer.from(int16Data.buffer);
                    const wavBuffer = Buffer.concat([createWavHeader(pcmBuffer.length), pcmBuffer]);

                    const transcript = await transcribeAudio(wavBuffer);
                    if (transcript && transcript.trim()) {
                        console.log(`🎤 Patient: ${transcript}`);
                        callState.messages.push({ role: "user", content: transcript });

                        const response = await getAIResponse(callState.messages);
                        console.log(`🤖 Agent: ${response}`);
                        callState.messages.push({ role: "assistant", content: response });

                        // Cannot play response mid-call - store it
                        callState.pendingResponses.push(response);
                        console.log(`⚠️ Cannot play audio mid-call. Response logged.`);
                    }
                } catch (e) {
                    console.error("Error:", e);
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
        if (greetingFile && existsSync(greetingFile)) unlinkSync(greetingFile);
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
            if (callState?.call) { callState.call.end(); json({ status: "hung_up" }); }
            else { json({ error: "Call not found" }, 404); }
        }
        else if (url.pathname === "/status") {
            const calls = Array.from(activeCalls.values()).map(c => ({
                id: c.id,
                phoneNumber: c.phoneNumber,
                patientId: c.patientId,
                status: c.status,
                duration: Date.now() - c.startTime,
                messages: c.messages.filter(m => m.role !== 'system'),
                pendingResponses: c.pendingResponses || []
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
    console.log(`
${"═".repeat(60)}
  STROKEGUARD WHATSAPP VOICE SERVER
${"═".repeat(60)}

🚀 Server: http://localhost:${PORT}

⚠️  KNOWN LIMITATION:
   baileys-caller does not support sending audio mid-call.
   The greeting plays, then responses are text-only.

   To fix this, you would need to:
   1. Fork baileys-caller and add call.sendAudio() method
   2. Or use Twilio/Vonage with WhatsApp Business API
   3. Or use a SIP gateway bridged to WhatsApp

Endpoints:
  POST /call    - { phoneNumber, patientId? }
  POST /hangup  - { callId }
  GET  /status  - List active calls with transcripts
`);
    initWhatsApp().catch(console.error);
});
