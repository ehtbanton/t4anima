#!/usr/bin/env python3
"""
StrokeGuard Autonomous Triage Agent (Gemini)

Runs continuously, scanning patient population for urgent cases.
When it finds a high-risk patient needing intervention:
1. Generates a clinical summary
2. Drafts a referral letter to stroke clinic
3. Creates an action list for the GP
4. Flags the patient in the system
5. Sends alerts (Slack/email ready)

This is work a junior doctor would do — now autonomous.
Uses Gemini 3.5 Flash Lite with function calling.
"""

import os
import json
import time
import requests
from datetime import datetime
from pathlib import Path

# Load API key from ~/.env if not in environment
if not os.environ.get("GEMINI_API_KEY"):
    env_file = Path.home() / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"\'')
                break

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY not found. Set it in ~/.env or environment.")

MODEL = "gemini-3.1-flash-lite"
BASE_URL = "https://generativelanguage.googleapis.com"

with open("data/patients_final.json") as f:
    PATIENTS = json.load(f)
    PATIENTS_BY_ID = {p["id"]: p for p in PATIENTS}

os.makedirs("output/referrals", exist_ok=True)
os.makedirs("output/alerts", exist_ok=True)
os.makedirs("output/actions", exist_ok=True)

# Agent's tools in Gemini format
TOOLS = [
    {
        "name": "get_high_risk_patients",
        "description": "Get list of patients with stroke risk above threshold who haven't been reviewed recently",
        "parameters": {
            "type": "object",
            "properties": {
                "risk_threshold": {
                    "type": "number",
                    "description": "Minimum stroke risk score (0-1) to flag. Default 0.15 (15%)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max patients to return"
                }
            }
        }
    },
    {
        "name": "get_patient_full_record",
        "description": "Get complete clinical record for a patient including all events, conditions, and investigations",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string", "description": "Patient ID"}
            },
            "required": ["patient_id"]
        }
    },
    {
        "name": "write_referral_letter",
        "description": "Generate and save a formal referral letter to stroke/TIA clinic for urgent review",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "urgency": {"type": "string", "enum": ["routine", "urgent", "2ww", "emergency"]},
                "clinical_summary": {"type": "string", "description": "Brief clinical picture"},
                "reason_for_referral": {"type": "string"},
                "specific_concerns": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["patient_id", "urgency", "clinical_summary", "reason_for_referral"]
        }
    },
    {
        "name": "create_gp_action_list",
        "description": "Create prioritized action list for the GP to complete before/alongside referral",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "actions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string"},
                            "priority": {"type": "string", "enum": ["immediate", "this_week", "routine"]},
                            "rationale": {"type": "string"}
                        }
                    }
                }
            },
            "required": ["patient_id", "actions"]
        }
    },
    {
        "name": "flag_patient_for_review",
        "description": "Flag a patient record for senior clinician review with reason",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "flag_type": {"type": "string", "enum": ["urgent_review", "medication_review", "investigation_needed", "safeguarding"]},
                "reason": {"type": "string"}
            },
            "required": ["patient_id", "flag_type", "reason"]
        }
    },
    {
        "name": "send_alert",
        "description": "Send alert to clinical team (Slack/email). Use for urgent cases needing immediate attention.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {"type": "string", "enum": ["slack_urgent", "slack_routine", "email_oncall", "email_gp"]},
                "subject": {"type": "string"},
                "message": {"type": "string"},
                "patient_id": {"type": "string"}
            },
            "required": ["channel", "subject", "message"]
        }
    },
    {
        "name": "log_decision",
        "description": "Log clinical reasoning for audit trail. Always log your decisions.",
        "parameters": {
            "type": "object",
            "properties": {
                "patient_id": {"type": "string"},
                "decision": {"type": "string"},
                "reasoning": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]}
            },
            "required": ["decision", "reasoning"]
        }
    },
    {
        "name": "complete_triage_round",
        "description": "Mark triage round as complete. Call this when you've reviewed all high-risk patients.",
        "parameters": {
            "type": "object",
            "properties": {
                "patients_reviewed": {"type": "integer"},
                "referrals_generated": {"type": "integer"},
                "alerts_sent": {"type": "integer"},
                "summary": {"type": "string"}
            },
            "required": ["patients_reviewed", "summary"]
        }
    }
]


def execute_tool(name: str, args: dict) -> str:
    """Execute a tool and return result."""

    if name == "get_high_risk_patients":
        threshold = args.get("risk_threshold", 0.15)
        limit = args.get("limit", 10)
        high_risk = [p for p in PATIENTS if p.get("stroke_risk", 0) > threshold]
        high_risk.sort(key=lambda p: p.get("stroke_risk", 0), reverse=True)
        result = []
        for p in high_risk[:limit]:
            conditions = [c["term"] for c in p.get("conditions", [])]
            result.append({
                "id": p["id"],
                "risk": round(p.get("stroke_risk", 0) * 100, 1),
                "age": p["age"],
                "conditions": conditions,
                "bp": f"{p['sbp']}/{p['dbp']}",
                "will_stroke": p.get("will_stroke", False)
            })
        return json.dumps(result, indent=2)

    elif name == "get_patient_full_record":
        patient = PATIENTS_BY_ID.get(args["patient_id"])
        if not patient:
            return json.dumps({"error": "Patient not found"})
        return json.dumps(patient, indent=2, default=str)

    elif name == "write_referral_letter":
        pid = args["patient_id"]
        patient = PATIENTS_BY_ID.get(pid, {})
        letter = f"""
STROKE/TIA CLINIC REFERRAL
==========================
Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
Urgency: {args['urgency'].upper()}

PATIENT DETAILS
---------------
ID: {pid}
Age: {patient.get('age', 'N/A')} | Sex: {patient.get('sex', 'N/A')}
BP: {patient.get('sbp', 'N/A')}/{patient.get('dbp', 'N/A')} mmHg

CLINICAL SUMMARY
----------------
{args['clinical_summary']}

REASON FOR REFERRAL
-------------------
{args['reason_for_referral']}

SPECIFIC CONCERNS
-----------------
{chr(10).join(f'• {c}' for c in args.get('specific_concerns', []))}

---
Generated by StrokeGuard AI Triage Agent
For senior review before sending
"""
        filepath = f"output/referrals/{pid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filepath, "w") as f:
            f.write(letter)
        print(f"  [REFERRAL] Written: {filepath}")
        return json.dumps({"status": "saved", "path": filepath})

    elif name == "create_gp_action_list":
        pid = args["patient_id"]
        actions_text = "\n".join(
            f"[{a['priority'].upper()}] {a['action']}\n   Rationale: {a['rationale']}"
            for a in args["actions"]
        )
        content = f"""
GP ACTION LIST - {pid}
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
{'='*50}

{actions_text}

---
Generated by StrokeGuard AI Triage Agent
"""
        filepath = f"output/actions/{pid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filepath, "w") as f:
            f.write(content)
        print(f"  [ACTIONS] Written: {filepath}")
        return json.dumps({"status": "saved", "path": filepath, "action_count": len(args["actions"])})

    elif name == "flag_patient_for_review":
        pid = args["patient_id"]
        print(f"  [FLAG] {pid}: {args['flag_type']} - {args['reason']}")
        return json.dumps({"status": "flagged", "patient_id": pid, "flag": args["flag_type"]})

    elif name == "send_alert":
        channel = args["channel"]
        print(f"  [ALERT → {channel}] {args['subject']}")
        filepath = f"output/alerts/{datetime.now().strftime('%Y%m%d_%H%M%S')}_{channel}.txt"
        with open(filepath, "w") as f:
            f.write(f"TO: {channel}\nSUBJECT: {args['subject']}\n\n{args['message']}")
        return json.dumps({"status": "sent", "channel": channel})

    elif name == "log_decision":
        pid = args.get("patient_id", "system")
        print(f"  [LOG] {pid}: {args['decision']} (confidence: {args.get('confidence', 'N/A')})")
        return json.dumps({"status": "logged"})

    elif name == "complete_triage_round":
        print(f"\n{'='*60}")
        print(f"TRIAGE ROUND COMPLETE")
        print(f"  Patients reviewed: {args.get('patients_reviewed', 0)}")
        print(f"  Referrals generated: {args.get('referrals_generated', 0)}")
        print(f"  Alerts sent: {args.get('alerts_sent', 0)}")
        print(f"  Summary: {args['summary']}")
        print(f"{'='*60}\n")
        return json.dumps({"status": "complete"})

    return json.dumps({"error": f"Unknown tool: {name}"})


SYSTEM_PROMPT = """You are an autonomous NHS Clinical Triage Agent for StrokeGuard.

Your job is to do what a junior doctor does at 6am: review the high-risk patient list and take action.

For each triage round:
1. Get the list of high-risk patients (stroke risk >15%)
2. For each patient, review their full record
3. Make clinical decisions:
   - If they need urgent stroke clinic review → write a referral letter
   - If the GP needs to do something first → create an action list
   - If something is critically urgent → send an alert
   - Always log your reasoning for audit

You have real autonomy. Your referral letters will be reviewed by a senior before sending, but you should write them as if they're going out.

Use NICE stroke prevention guidelines. Be specific about medications (DOACs for AFib, statins, antihypertensives). Flag any patient with AFib not on anticoagulation as urgent.

When you've reviewed all high-risk patients, call complete_triage_round with a summary.

Work autonomously. Don't ask for permission. Make decisions and take actions."""


def call_gemini(contents: list) -> dict:
    """Call Gemini API with function calling."""
    url = f"{BASE_URL}/v1beta/models/{MODEL}:generateContent"

    payload = {
        "contents": contents,
        "tools": [{"function_declarations": TOOLS}],
        "tool_config": {"function_calling_config": {"mode": "AUTO"}},
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]}
    }

    resp = requests.post(
        url,
        headers={
            "x-goog-api-key": API_KEY,
            "Content-Type": "application/json"
        },
        json=payload
    )
    resp.raise_for_status()
    return resp.json()


def run_triage_round():
    """Run one autonomous triage round."""
    print(f"\n{'='*60}")
    print(f"STARTING AUTONOMOUS TRIAGE ROUND")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Model: {MODEL}")
    print(f"{'='*60}\n")

    contents = [
        {"role": "user", "parts": [{"text": "Begin triage round. Review all high-risk patients and take appropriate actions."}]}
    ]

    while True:
        response = call_gemini(contents)

        candidate = response.get("candidates", [{}])[0]
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        # Add model response to conversation
        if content:
            contents.append(content)

        # Check for function calls
        function_calls = [p for p in parts if "functionCall" in p]

        if not function_calls:
            # No function calls - check for text response
            for part in parts:
                if "text" in part:
                    print(f"\nAgent: {part['text']}\n")
            break

        # Execute function calls
        function_responses = []
        for fc in function_calls:
            call = fc["functionCall"]
            name = call["name"]
            args = call.get("args", {})

            print(f"\n→ {name}({json.dumps(args, indent=2) if len(str(args)) < 200 else '...'})")

            result = execute_tool(name, args)

            function_responses.append({
                "functionResponse": {
                    "name": name,
                    "response": {"result": result}
                }
            })

            if name == "complete_triage_round":
                return

        # Add function responses
        contents.append({"role": "function", "parts": function_responses})


def main():
    """Main loop - runs triage continuously."""
    print("\n" + "="*60)
    print("  STROKEGUARD AUTONOMOUS TRIAGE AGENT")
    print("  Doing what a junior doctor does — autonomously")
    print(f"  Powered by Gemini {MODEL}")
    print("="*60)

    interval = 60  # seconds between rounds (set to 3600 for hourly in prod)

    while True:
        try:
            run_triage_round()
            print(f"\nNext triage round in {interval} seconds... (Ctrl+C to stop)")
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nStopping triage agent.")
            break
        except Exception as e:
            print(f"\nError in triage round: {e}")
            import traceback
            traceback.print_exc()
            print("Retrying in 30 seconds...")
            time.sleep(30)


if __name__ == "__main__":
    main()
