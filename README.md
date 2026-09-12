# NHS StrokeGuard AI

**Proactive AI Voice Agent for Stroke Prevention**

StrokeGuard is an NHS-integrated AI system that proactively contacts high-risk stroke patients via phone calls, conducts clinical welfare checks, and triggers real-time clinical actions - all through natural voice conversation.

## The Problem

Every year, **100,000 people in the UK have a stroke**, with many preventable through early intervention. GPs face impossible workloads - there's no way to manually call every at-risk patient. High-risk cases slip through the cracks.

## Our Solution

StrokeGuard autonomously identifies and calls high-risk patients, conducting intelligent clinical conversations that:

- Explain their stroke risk factors in plain language
- Check medication compliance
- Identify emerging symptoms
- Schedule GP appointments in real-time
- Flag urgent cases for immediate clinical review
- All documented automatically in clinical records

## Key Innovation: WhatsApp Voice Calls via VAPI

We built a **custom fork of baileys-caller** with mid-call audio injection, enabling:

1. **Real phone calls** - Not browser demos. Actual WhatsApp calls to real phone numbers
2. **Full VAPI integration** - Deepgram STT, GPT-4o conversation, ElevenLabs TTS
3. **Bidirectional audio bridge** - Patient speech → VAPI → AI response → Patient's phone

This is production-ready voice AI that reaches patients where they are.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Voice AI | VAPI (Deepgram + GPT-4o + ElevenLabs) |
| WhatsApp Calls | Custom baileys-caller fork with sendAudio() |
| Clinical Agent | Gemini 3.6 Flash with OpenAI Agents SDK |
| Dashboard | NHS-styled HTML/JS with live SSE updates |
| Patient Data | 50k synthetic patient records |
| NHS Integration | NHS-SIM live simulation connection |

## Demo

```bash
./start.sh
```

Open http://localhost:8000

### Demo Options

1. **Demo Mode** - Scripted presentation showing the full flow
2. **Browser Call** - Live VAPI voice agent through your microphone
3. **WhatsApp Call** - Real phone calls (requires linked WhatsApp device)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    StrokeGuard Dashboard                        │
│  (Patient list, risk scores, clinical timeline, live actions)   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API Server (Flask)                          │
│  • Patient data API          • Clinical actions (FHIR-ready)    │
│  • VAPI tool webhooks        • NHS-SIM integration              │
│  • Codex agent (Gemini)      • SSE real-time updates            │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌─────────────────┐   ┌─────────────────────┐
│ Browser Call  │   │  WhatsApp Call  │   │   NHS-SIM (Live)    │
│ (VAPI Web SDK)│   │  Voice Server   │   │  Patient simulation │
└───────────────┘   └─────────────────┘   └─────────────────────┘
                              │
                              ▼
                    ┌─────────────────┐
                    │ baileys-caller  │
                    │ (forked: audio) │
                    └─────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐   ┌─────────────────┐   ┌─────────────────────┐
│ Patient Phone │   │   VAPI Cloud    │   │ Real-time Clinical  │
│ (WhatsApp)    │   │ (STT+LLM+TTS)   │   │ Actions (dashboard) │
└───────────────┘   └─────────────────┘   └─────────────────────┘
```

## Clinical Actions (Live Demo)

During a call, the AI can trigger:

- 📋 Pull full patient record
- ⚠️ Explain risk factors  
- 💊 Check medication compliance
- 📅 Schedule GP appointments
- 🔬 Order investigations
- 🏥 Refer to specialists
- 📝 Add clinical notes
- 🚨 **Flag urgent review** (triggers 999 alert overlay)

All actions appear live in the dashboard's "Agent Actions" panel.

## Team

Built for Anima Hackathon 2026

## Future Work

- Integration with NHS Spine for real patient data
- Multi-language support (Welsh, British Sign Language)
- Automated follow-up scheduling based on outcomes
- Integration with GP clinical systems (EMIS, SystmOne)
