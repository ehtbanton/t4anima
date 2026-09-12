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

const TOOL_PROTOCOL = `
CLINICAL TOOLS — you can access the live medical record system. TOOL PROTOCOL (strict):
1. When you need record data or want to take a clinical action, call the matching tool.
2. Immediately after calling a tool, say ONLY a short holding phrase like "Let me just pull that up." Do NOT state any medical facts or confirm any action yet.
3. The real result arrives moments later as a message beginning "[SYSTEM TOOL RESULT". It is machine data, NOT patient speech. When you see it, answer the patient using ONLY facts from that result, in warm plain English sentences.
4. NEVER read JSON, field names, or IDs aloud. NEVER invent medical data or pretend an action succeeded — if no result has arrived yet, say you are still checking.`;

function getSystemPrompt(patient, patientId) {
    const base = patient
        ? `You are an NHS StrokeGuard clinical AI agent making a proactive welfare call.

PATIENT: ${patient.id}
Age: ${patient.age}, Sex: ${patient.sex}
Blood Pressure: ${patient.sbp}/${patient.dbp} mmHg
Stroke Risk Score: ${((patient.stroke_risk || 0) * 100).toFixed(1)}%
Conditions: ${patient.conditions?.map(c => c.term).join(", ") || "None"}
Smoker: ${patient.smoking ? "Yes" : "No"}`
        : `You are an NHS StrokeGuard clinical AI agent making a proactive welfare call.${patientId ? `\n\nPATIENT: ${patientId} (use tools to pull their record)` : ""}`;

    return `${base}

YOUR ROLE: Conduct a clinical welfare check. You can:
- Pull their full record, risk explanation, medications, and recent results
- Explain their stroke risk factors in plain language
- Schedule GP appointments, order investigations, refer to specialists, add clinical notes
- Flag the patient for urgent clinical review if concerned
${TOOL_PROTOCOL}

COMMUNICATION:
- Warm, professional, reassuring
- Plain English, no jargon
- 2-3 sentences per response

EMERGENCY: If sudden weakness, facial drooping, slurred speech, severe headache, chest pain - tell them to CALL 999 IMMEDIATELY, and call flag_urgent_review.`;
}

// Tools mirror the handlers in api_server.py /api/vapi/tool (async: results are
// injected back over the websocket after local execution)
const CLINICAL_TOOLS = [
    { name: "get_full_patient_record", description: "Fetch the patient's full medical record: conditions, medications, investigations, hospital admissions", parameters: { type: "object", properties: { patient_id: { type: "string" } } } },
    { name: "get_risk_explanation", description: "Get a structured breakdown of the patient's stroke risk factors", parameters: { type: "object", properties: { patient_id: { type: "string" } } } },
    { name: "check_medication_compliance", description: "Check the patient's current medications and whether they are collecting them", parameters: { type: "object", properties: { patient_id: { type: "string" } } } },
    { name: "check_recent_results", description: "Check the patient's recent test results and outstanding investigations", parameters: { type: "object", properties: { patient_id: { type: "string" } } } },
    { name: "schedule_gp_appointment", description: "Book a GP appointment for the patient", parameters: { type: "object", properties: { urgency: { type: "string", enum: ["routine", "urgent", "same-day"] }, reason: { type: "string" } }, required: ["urgency", "reason"] } },
    { name: "request_medication_review", description: "Request a pharmacist/GP medication review", parameters: { type: "object", properties: { concern: { type: "string" }, priority: { type: "string", enum: ["routine", "urgent"] } }, required: ["concern"] } },
    { name: "order_investigation", description: "Order a clinical investigation or test", parameters: { type: "object", properties: { test_type: { type: "string" }, indication: { type: "string" } }, required: ["test_type", "indication"] } },
    { name: "refer_to_specialist", description: "Refer the patient to a specialist service", parameters: { type: "object", properties: { specialty: { type: "string" }, urgency: { type: "string", enum: ["routine", "urgent", "2-week-wait"] }, reason: { type: "string" } }, required: ["specialty", "reason"] } },
    { name: "add_clinical_note", description: "Add a note to the patient's clinical record", parameters: { type: "object", properties: { note: { type: "string" }, category: { type: "string" } }, required: ["note"] } },
    { name: "flag_urgent_review", description: "Flag the patient for URGENT clinical review (possible emergency)", parameters: { type: "object", properties: { reason: { type: "string" }, symptoms: { type: "string" } }, required: ["reason"] } },
];

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
const API_SERVER = "http://localhost:8000";

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
                    messages: [{ role: "system", content: getSystemPrompt(patient, patientId) }],
                    tools: CLINICAL_TOOLS.map(t => ({ type: "function", async: true, function: t })),
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

// Execute a tool locally via the Flask webhook (fires the dashboard's SSE
// tool_call events), then inject the result back into the VAPI conversation.
async function executeToolCall(callState, tc) {
    const name = tc.function?.name || tc.name;
    const args = tc.function?.arguments || tc.arguments || {};
    console.log(`🔧 ${callState.id}: tool ${name}(${JSON.stringify(args)})`);

    let result = JSON.stringify({ error: "tool execution failed" });
    try {
        const r = await fetch(`${API_SERVER}/api/vapi/tool`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                message: { type: "function-call", functionCall: { name, parameters: args } },
                call: { id: callState.vapiCallId },
                patientId: callState.patientId,
            }),
        });
        result = (await r.json()).result ?? result;
    } catch (e) {
        console.error(`🔧 ${callState.id}: webhook error:`, e.message);
    }

    callState.messages.push({ role: "tool", content: `${name} → ${String(result).slice(0, 200)}` });

    // Queue the result; it is injected after the assistant's holding phrase
    // finishes (injecting mid-turn gets swallowed by VAPI).
    callState.pendingInjection = {
        type: "add-message",
        message: { role: "user", content: `[SYSTEM TOOL RESULT — not spoken by the patient] ${name} returned: ${result}` },
        triggerResponseEnabled: true,
    };
    // Failsafe: inject even if no speech-update arrives
    setTimeout(() => injectPendingResult(callState, "failsafe"), 6000);
}

function injectPendingResult(callState, source) {
    const inj = callState.pendingInjection;
    const ws = callState.ws;
    if (!inj || !ws || ws.readyState !== WebSocket.OPEN) return;
    callState.pendingInjection = null;
    ws.send(JSON.stringify(inj));
    console.log(`💉 ${callState.id}: tool result injected (${source})`);
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
        pendingInjection: null,
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
                            if (String(msg.transcript).startsWith("[SYSTEM TOOL RESULT")) return;
                            const role = msg.role === "assistant" ? "assistant" : "user";
                            const icon = role === "assistant" ? "🤖" : "🎤";
                            console.log(`${icon} ${role}: ${msg.transcript}`);
                            callState.messages.push({ role, content: msg.transcript });
                        } else if (msg.type === "tool-calls") {
                            (msg.toolCallList || []).forEach(tc => executeToolCall(callState, tc));
                        } else if (msg.type === "speech-update" && msg.role === "assistant" && msg.status === "stopped") {
                            // Holding phrase finished — safe to inject a queued tool result
                            setTimeout(() => injectPendingResult(callState, "speech-end"), 400);
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
                messages: c.messages.filter(m => m.role !== "tool"),
                toolCalls: c.messages.filter(m => m.role === "tool").map(m => m.content),
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
