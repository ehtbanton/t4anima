#!/usr/bin/env python3
"""
StrokeGuard Voice Agent - ElevenLabs Conversational AI
Drives patient support calls with real-time voice + tool calling.
"""

import os
import json
import asyncio
from datetime import datetime
from pathlib import Path

# Load API keys from ~/.env
env_file = Path.home() / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            if key.strip() and not os.environ.get(key.strip()):
                os.environ[key.strip()] = val.strip().strip('"\'')

from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import Conversation, ClientTools

# Load patient data
DATA_FILE = 'data/patients_50k.json' if os.path.exists('data/patients_50k.json') else 'data/patients_final.json'
with open(DATA_FILE) as f:
    PATIENTS = json.load(f)
    PATIENTS_BY_ID = {p['id']: p for p in PATIENTS}
    print(f"Loaded {len(PATIENTS)} patients")

# Initialize ElevenLabs client
client = ElevenLabs(api_key=os.environ.get("ELEVENLABS_API_KEY"))


# ============================================================================
# PATIENT SUPPORT TOOLS (called during voice conversation)
# ============================================================================

def lookup_patient(patient_id: str) -> str:
    """Look up a patient's record by their ID."""
    patient = PATIENTS_BY_ID.get(patient_id.upper())
    if not patient:
        return json.dumps({"error": f"No patient found with ID {patient_id}"})

    conditions = [c["term"] for c in patient.get("conditions", [])]
    return json.dumps({
        "id": patient["id"],
        "age": patient["age"],
        "sex": patient["sex"],
        "blood_pressure": f"{patient['sbp']}/{patient['dbp']}",
        "smoking": patient["smoking"],
        "stroke_risk_percent": round(patient.get("stroke_risk", 0) * 100, 1),
        "conditions": conditions,
        "total_appointments": len(patient.get("events", []))
    })


def get_risk_explanation(patient_id: str) -> str:
    """Explain a patient's stroke risk factors in plain language."""
    patient = PATIENTS_BY_ID.get(patient_id.upper())
    if not patient:
        return json.dumps({"error": "Patient not found"})

    factors = []
    conditions = [c["term"].lower() for c in patient.get("conditions", [])]

    if any("atrial" in c or "fibrillation" in c for c in conditions):
        factors.append("You have atrial fibrillation, which significantly increases stroke risk because it can cause blood clots to form in the heart.")
    if any("hypertension" in c for c in conditions):
        factors.append("You have high blood pressure, which puts extra strain on blood vessels and increases stroke risk.")
    if any("diabetes" in c for c in conditions):
        factors.append("Diabetes affects your blood vessels and can increase stroke risk over time.")
    if patient["smoking"]:
        factors.append("Smoking damages blood vessels and significantly increases your stroke risk.")
    if patient["sbp"] > 140:
        factors.append(f"Your blood pressure reading of {patient['sbp']}/{patient['dbp']} is elevated and needs attention.")
    if patient["age"] > 75:
        factors.append("Age is a factor - stroke risk increases as we get older, so prevention is extra important.")

    if not factors:
        factors.append("Your overall risk profile looks good. Keep up the healthy habits!")

    return json.dumps({
        "patient_id": patient["id"],
        "risk_percent": round(patient.get("stroke_risk", 0) * 100, 1),
        "explanations": factors,
        "recommendation": "urgent follow-up" if patient.get("stroke_risk", 0) > 0.2 else "routine monitoring"
    })


def check_recent_appointments(patient_id: str) -> str:
    """Check a patient's recent appointments and investigations."""
    patient = PATIENTS_BY_ID.get(patient_id.upper())
    if not patient:
        return json.dumps({"error": "Patient not found"})

    events = patient.get("events", [])[-10:]  # Last 10 events
    recent = []
    for e in events:
        recent.append({
            "date": e.get("date"),
            "type": e.get("type"),
            "title": e.get("title"),
            "detail": e.get("detail")
        })

    return json.dumps({
        "patient_id": patient["id"],
        "recent_appointments": recent,
        "total_records": len(patient.get("events", []))
    })


def schedule_followup(patient_id: str, urgency: str, reason: str) -> str:
    """Schedule a follow-up appointment for a patient."""
    patient = PATIENTS_BY_ID.get(patient_id.upper())
    if not patient:
        return json.dumps({"error": "Patient not found"})

    # In production, this would integrate with a booking system
    os.makedirs("output/followups", exist_ok=True)
    filepath = f"output/followups/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    booking = {
        "patient_id": patient_id,
        "urgency": urgency,
        "reason": reason,
        "requested_at": datetime.now().isoformat(),
        "status": "pending_confirmation"
    }

    with open(filepath, "w") as f:
        json.dump(booking, f, indent=2)

    return json.dumps({
        "status": "scheduled",
        "patient_id": patient_id,
        "urgency": urgency,
        "message": f"Follow-up request logged. {'We will call you within 24 hours to confirm.' if urgency == 'urgent' else 'You will receive a booking confirmation within 3 working days.'}"
    })


def request_medication_review(patient_id: str, concern: str) -> str:
    """Request a medication review for a patient."""
    patient = PATIENTS_BY_ID.get(patient_id.upper())
    if not patient:
        return json.dumps({"error": "Patient not found"})

    os.makedirs("output/med_reviews", exist_ok=True)
    filepath = f"output/med_reviews/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    review = {
        "patient_id": patient_id,
        "concern": concern,
        "requested_at": datetime.now().isoformat(),
        "conditions": [c["term"] for c in patient.get("conditions", [])]
    }

    with open(filepath, "w") as f:
        json.dump(review, f, indent=2)

    return json.dumps({
        "status": "requested",
        "message": "Your medication review request has been logged. A pharmacist or your GP will review this within 48 hours."
    })


# ============================================================================
# VOICE AGENT CONFIGURATION
# ============================================================================

AGENT_PROMPT = """You are a friendly NHS patient support assistant for StrokeGuard, a stroke prevention service.

You're calling patients to:
1. Check in on their health and wellbeing
2. Review their stroke risk factors
3. Answer questions about their conditions
4. Help schedule follow-up appointments
5. Address medication concerns

IMPORTANT GUIDELINES:
- Be warm, empathetic, and reassuring - many patients are anxious about their health
- Speak in plain English, avoid medical jargon
- If a patient mentions chest pain, sudden weakness, speech problems, or severe headache, tell them to call 999 immediately
- Always verify the patient ID at the start of the call
- Use the tools to look up their records before discussing specifics
- Offer to schedule follow-ups proactively if their risk is elevated

Start by greeting the patient warmly and asking them to confirm their patient ID for security."""


async def run_voice_agent(agent_id: str = None):
    """Run the voice agent conversation."""

    # Register tools
    tools = ClientTools()
    tools.register("lookup_patient", lookup_patient)
    tools.register("get_risk_explanation", get_risk_explanation)
    tools.register("check_recent_appointments", check_recent_appointments)
    tools.register("schedule_followup", schedule_followup)
    tools.register("request_medication_review", request_medication_review)

    # Create or use existing agent
    if not agent_id:
        # Create a new agent with our configuration
        agent = client.conversational_ai.agents.create(
            name="StrokeGuard Patient Support",
            conversation_config={
                "agent": {
                    "prompt": {
                        "prompt": AGENT_PROMPT
                    },
                    "first_message": "Hello! This is the StrokeGuard patient support line. I'm here to help with any questions about your health and stroke prevention. Could you please confirm your patient ID so I can pull up your records?",
                    "language": "en"
                },
                "tts": {
                    "voice_id": "21m00Tcm4TlvDq8ikWAM"  # Rachel - warm, professional
                }
            },
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "lookup_patient",
                        "description": "Look up a patient's medical record by their ID",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "patient_id": {"type": "string", "description": "The patient's ID (e.g., P00001)"}
                            },
                            "required": ["patient_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "get_risk_explanation",
                        "description": "Get a plain-language explanation of a patient's stroke risk factors",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "patient_id": {"type": "string"}
                            },
                            "required": ["patient_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "check_recent_appointments",
                        "description": "Check a patient's recent appointments and test results",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "patient_id": {"type": "string"}
                            },
                            "required": ["patient_id"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "schedule_followup",
                        "description": "Schedule a follow-up appointment for the patient",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "patient_id": {"type": "string"},
                                "urgency": {"type": "string", "enum": ["routine", "soon", "urgent"]},
                                "reason": {"type": "string", "description": "Reason for the follow-up"}
                            },
                            "required": ["patient_id", "urgency", "reason"]
                        }
                    }
                },
                {
                    "type": "function",
                    "function": {
                        "name": "request_medication_review",
                        "description": "Request a medication review for the patient",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "patient_id": {"type": "string"},
                                "concern": {"type": "string", "description": "The patient's medication concern"}
                            },
                            "required": ["patient_id", "concern"]
                        }
                    }
                }
            ]
        )
        agent_id = agent.agent_id
        print(f"Created agent: {agent_id}")

    # Start conversation
    conversation = Conversation(
        client=client,
        agent_id=agent_id,
        client_tools=tools,
        on_agent_response=lambda text: print(f"\n🤖 Agent: {text}"),
        on_user_transcript=lambda text: print(f"\n👤 Patient: {text}"),
        on_tool_call=lambda name, args: print(f"\n🔧 Tool: {name}({args})")
    )

    print("\n" + "=" * 60)
    print("  STROKEGUARD VOICE AGENT")
    print("  Patient Support Line")
    print("=" * 60)
    print("\nStarting voice conversation...")
    print("Speak into your microphone. Press Ctrl+C to end.\n")

    try:
        await conversation.start()
        await conversation.wait_for_completion()
    except KeyboardInterrupt:
        print("\n\nEnding call...")
        await conversation.end()

    print("\nCall ended.")


def main():
    """Entry point."""
    if not os.environ.get("ELEVENLABS_API_KEY"):
        print("ERROR: ELEVENLABS_API_KEY not set")
        print("Add it to ~/.env or export it")
        return

    print("ElevenLabs Voice Agent initialized")
    asyncio.run(run_voice_agent())


if __name__ == "__main__":
    main()
