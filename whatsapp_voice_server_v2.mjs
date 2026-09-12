/**
 * WhatsApp Voice Call Server v3 — VAPI bridge
 *
 * WhatsApp audio (baileys-caller fork) <-> VAPI websocket transport.
 * VAPI runs the whole voice loop (Deepgram STT + GPT-4o + 11labs TTS),
 * exactly like the browser call in strokeguard.html.
 *
 * API:
 *   POST /call { phoneNumber, patientId? }
 *   POST /hangup { callId }
 *   GET  /status
 */

import { VoipClient } from "./lib/baileys-caller-fork/dist/index.mjs";
import { createServer } from "http";
import { join } from "path";

const PORT = 3001;
const AUTH_DIR = "./whatsapp_session";
const SAMPLE_RATE = 16000;

import { config } from "dotenv";
config({ path: join(process.env.HOME, ".env") });

// VAPI private key (hard-coded for the team; env var overrides if set)
const VAPI_API_KEY = process.env.VAPI_API_KEY || "8e11faa8-fb00-4d8c-8357-8b54ecfb85fe";

// LLM behind the VAPI agent: OpenAI if a key is present, otherwise Gemini
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const MODEL_CONFIG = OPENAI_API_KEY
    ? { provider: "openai", model: "gpt-4o" }
    : { provider: "google", model: "gemini-2.0-flash" };
console.log(`LLM: ${MODEL_CONFIG.provider}/${MODEL_CONFIG.model}${!OPENAI_API_KEY && !GEMINI_API_KEY ? " (no local LLM key found — using VAPI-managed Gemini)" : ""}`);

const activeCalls = new Map();
let voipClient = null;

// Patient data
let patients = [];
try {
    const fs = await import("fs");
    patients = JSON.parse(fs.readFileSync("data/patients_50k.json", "utf-8"));
    console.log(`Loaded ${patients.length} patients`);
} catch {
    console.log("No patient data loaded");
}
const patientsById = new Map(patients.map(p => [p.id, p]));

function getSystemPrompt(patient) {
    if (!patient) {
        return `You are an NHS StrokeGuard clinical AI agent making a proactive welfare call.

YOUR ROLE: Conduct a clinical welfare check. You can:
- Explain stroke risk factors in plain language
- Answer questions about their health
- Offer to schedule a GP follow-up if needed

COMMUNICATION:
- Warm, professional, reassuring
- Plain English, no jargon
- 2-3 sentences per response

EMERGENCY: If sudden weakness, facial drooping, slurred speech, severe headache, chest pain - tell them to CALL 999 IMMEDIATELY.`;
    }
    const conditions = patient.conditions?.map(c => c.term).join(", ") || "None";
    const riskPct = ((patient.stroke_risk || 0) * 100).toFixed(1);
    return `You are an NHS StrokeGuard clinical AI agent making a proactive welfare call.

PATIENT: ${patient.id}
Age: ${patient.age}, Sex: ${patient.sex}
Blood Pressure: ${patient.sbp}/${patient.dbp} mmHg
Stroke Risk Score: ${riskPct}%
Conditions: ${conditions}
Smoker: ${patient.smoking ? "Yes" : "No"}

YOUR ROLE: Conduct a clinical welfare check. You can:
- Explain their stroke risk factors in plain language
- Discuss their conditions and what they mean
- Answer questions about their health
- Offer to schedule a GP follow-up if needed

COMMUNICATION:
- Warm, professional, reassuring
- Plain English, no jargon
- 2-3 sentences per response

EMERGENCY: If sudden weakness, facial drooping, slurred speech, severe headache, chest pain - tell them to CALL 999 IMMEDIATELY.`;
}

// ── PCM conversion helpers ──────────────────────────────────────────────
function float32ToInt16Buffer(f32) {
    const out = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]));
        out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return Buffer.from(out.buffer);
}

function int16ToFloat32(buf) {
    const i16 = new Int16Array(buf.buffer, buf.byteOffset, Math.floor(buf.byteLength / 2));
    const out = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) out[i] = i16[i] / 32768;
    return out;
}

// ── VAPI websocket call ─────────────────────────────────────────────────
async function createVapiWebsocketCall(patient, patientId) {
    const greeting = patientId
        ? `Hello, this is the NHS StrokeGuard patient support service. Am I speaking with ${patientId}? I'm calling to check in on how you're doing with your health.`
        : `Hello, this is the NHS StrokeGuard patient support service. I'm calling to check in on how you're doing with your health.`;

    const res = await fetch("https://api.vapi.ai/call", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Authorization": `Bearer ${VAPI_API_KEY}`,
        },
        body: JSON.stringify({
            transport: {
                provider: "vapi.websocket",
                audioFormat: {
                    format: "pcm_s16le",
                    container: "raw",
                    sampleRate: SAMPLE_RATE,
                },
            },
            assistant: {
                transcriber: { provider: "deepgram", model: "nova-2", language: "en-GB" },
                model: {
                    ...MODEL_CONFIG,
                    messages: [{ role: "system", content: getSystemPrompt(patient) }],
                },
                voice: { provider: "11labs", voiceId: "TxGEqnHWrfWFTfGW9XjX" },
                firstMessage: greeting,
            },
        }),
    });

    if (!res.ok) {
        throw new Error(`VAPI call create failed: ${res.status} ${await res.text()}`);
    }

    const data = await res.json();
    const wsUrl = data.transport?.websocketCallUrl;
    if (!wsUrl) throw new Error("No websocketCallUrl in VAPI response: " + JSON.stringify(data));
    return { vapiCallId: data.id, wsUrl };
}

// ── WhatsApp init ───────────────────────────────────────────────────────
async function initWhatsApp() {
    if (voipClient) return voipClient;
    console.log("Initializing WhatsApp VoIP client (forked)...");
    voipClient = new VoipClient({ authDir: AUTH_DIR });
    await voipClient.connect();
    console.log("✅ WhatsApp connected");
    return voipClient;
}

// ── Call flow ───────────────────────────────────────────────────────────
async function makeCall(phoneNumber, patientId) {
    const client = await initWhatsApp();
    const patient = patientsById.get(patientId);
    const cleanNumber = phoneNumber.replace(/[^0-9]/g, "");
    const callId = `call_${Date.now()}`;

    console.log(`📞 Calling +${cleanNumber} (Patient: ${patientId || "unknown"}) [${callId}]`);

    const call = await client.call(cleanNumber, {
        audioSource: "silence",
        durationMs: 600000, // 10 min max
    });

    const callState = {
        id: callId,
        phoneNumber: cleanNumber,
        patientId,
        status: "ringing",
        startTime: Date.now(),
        call,
        ws: null,
        vapiCallId: null,
        messages: [],
    };
    activeCalls.set(callId, callState);

    call.on("ringing", () => {
        console.log(`📳 ${callId}: Ringing...`);
        callState.status = "ringing";
    });

    call.on("connected", async () => {
        console.log(`✅ ${callId}: Connected! Starting VAPI bridge...`);
        callState.status = "connected";

        try {
            const { vapiCallId, wsUrl } = await createVapiWebsocketCall(patient, patientId);
            callState.vapiCallId = vapiCallId;
            console.log(`🔗 VAPI call ${vapiCallId}`);

            const ws = new WebSocket(wsUrl);
            ws.binaryType = "arraybuffer";
            callState.ws = ws;

            ws.onopen = () => {
                console.log(`🌐 ${callId}: VAPI websocket open — agent will speak first`);
                callState.status = "in-conversation";
            };

            ws.onmessage = (event) => {
                if (typeof event.data === "string") {
                    // JSON control/transcript messages
                    try {
                        const msg = JSON.parse(event.data);
                        if (msg.type === "transcript" && msg.transcriptType === "final") {
                            const role = msg.role === "assistant" ? "assistant" : "user";
                            const icon = role === "assistant" ? "🤖" : "🎤";
                            console.log(`${icon} ${role}: ${msg.transcript}`);
                            callState.messages.push({ role, content: msg.transcript });
                        } else if (msg.type === "status-update" && (msg.status === "ended" || msg.callStatus === "ended")) {
                            console.log(`🔚 ${callId}: VAPI ended the conversation`);
                            call.end();
                        }
                    } catch { /* non-JSON text frame, ignore */ }
                } else {
                    // Binary = 16kHz s16le PCM from VAPI (agent's voice) → into WhatsApp
                    const f32 = int16ToFloat32(Buffer.from(event.data));
                    if (f32.length > 0) call.sendAudio(f32);
                }
            };

            ws.onerror = (e) => console.error(`⚠️ ${callId}: VAPI ws error:`, e?.message || e);
            ws.onclose = () => {
                console.log(`🌐 ${callId}: VAPI websocket closed`);
                if (callState.status !== "ended") call.end();
            };
        } catch (e) {
            console.error(`❌ ${callId}: VAPI bridge failed:`, e.message);
            call.end();
        }
    });

    // WhatsApp inbound audio (patient's voice) → VAPI
    call.on("audio", (pcm) => {
        const ws = callState.ws;
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(float32ToInt16Buffer(pcm));
        }
    });

    call.on("ended", (reason) => {
        console.log(`📴 ${callId}: Ended - ${reason}`);
        callState.status = "ended";
        callState.endReason = reason;
        const ws = callState.ws;
        if (ws && ws.readyState === WebSocket.OPEN) {
            try { ws.send(JSON.stringify({ type: "hangup" })); } catch { }
            try { ws.close(); } catch { }
        }
        callState.ws = null;
        setTimeout(() => activeCalls.delete(callId), 60000);
    });

    return { callId, status: "initiated" };
}

// ── HTTP server ─────────────────────────────────────────────────────────
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
            json({ status: "ok", whatsapp: !!voipClient, engine: "vapi" });
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
                vapiCallId: c.vapiCallId,
                duration: Date.now() - c.startTime,
                audioQueueLength: c.call?.getQueueLength?.() ?? 0,
                messages: c.messages,
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
  STROKEGUARD WHATSAPP VOICE SERVER v3 — VAPI bridge
${"═".repeat(60)}

🚀 http://localhost:${PORT}
   POST /call    { phoneNumber, patientId? }
   POST /hangup  { callId }
   GET  /status

Initializing WhatsApp...
`);
    initWhatsApp().catch(console.error);
});
