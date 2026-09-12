#!/usr/bin/env python3
"""
StrokeGuard AI - API Server with LLM Integration
Provides real-time risk analysis + Patient Explorer Codex (Gemini)
"""

from flask import Flask, jsonify, request, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import json
import os
import time
import requests
from datetime import datetime
from pathlib import Path

app = Flask(__name__, static_folder='.')
CORS(app)

# ============================================================================
# NHS-SIM LIVE SIMULATION CONNECTION
# ============================================================================
NHS_SIM_API_KEY = "sim_6d18b9f8f08928852d3c6aec0136f3015bd8232b4bd14dd6"
NHS_SIM_BASE_URL = "https://sim.animahacks.com"
NHS_SIM_WORLD = "team-11e9ba55ce83"

def sim_fetch(endpoint, params=None):
    """Fetch data from the NHS-SIM API."""
    try:
        headers = {"Authorization": f"Bearer {NHS_SIM_API_KEY}"}
        url = f"{NHS_SIM_BASE_URL}{endpoint}"
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        print(f"[SIM] Error fetching {endpoint}: {e}")
        return None

def sim_post_action(site, action_data):
    """Post an action to the NHS-SIM API."""
    try:
        headers = {"Authorization": f"Bearer {NHS_SIM_API_KEY}", "Content-Type": "application/json"}
        url = f"{NHS_SIM_BASE_URL}/api/sites/{site}/actions"
        resp = requests.post(url, headers=headers, json=action_data, timeout=10)
        return resp.json() if resp.status_code == 200 else {"error": resp.text}
    except Exception as e:
        print(f"[SIM] Error posting action to {site}: {e}")
        return {"error": str(e)}

def get_sim_patient_ehr(patient_id):
    """Get full EHR record for a patient from simulation."""
    data = sim_fetch(f"/api/sites/gp/view", {"patient": patient_id, "limit": 100})
    if not data:
        return None

    ehr = None
    resources = []
    for r in data.get("resources", []):
        if r.get("kind") == "ehr-record":
            ehr = r.get("data", {})
        elif r.get("patientId") == patient_id:
            resources.append(r)

    return {"ehr": ehr, "resources": resources, "sim_time": data.get("now")}

def get_sim_all_patients():
    """Get list of all patients with resources from simulation."""
    patients = {}

    # Fetch from GP view
    data = sim_fetch("/api/sites/gp/view", {"limit": 200})
    if data:
        for r in data.get("resources", []):
            pid = r.get("patientId")
            if pid and pid.startswith("SIM-"):
                if pid not in patients:
                    patients[pid] = {"id": pid, "resources": [], "conditions": [], "priority": "routine"}
                patients[pid]["resources"].append(r)

                # Extract conditions from EHR
                if r.get("kind") == "ehr-record":
                    problems = r.get("data", {}).get("problems", [])
                    patients[pid]["conditions"] = [{"term": p["term"], "date": p["date"], "status": p["status"]} for p in problems]

                # Track highest priority
                if r.get("priority") == "urgent":
                    patients[pid]["priority"] = "urgent"

    return list(patients.values())

print(f"NHS-SIM connected: {NHS_SIM_BASE_URL} (world: {NHS_SIM_WORLD})")

# Load Gemini API key
if not os.environ.get("GEMINI_API_KEY"):
    env_file = Path.home() / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GEMINI_API_KEY="):
                os.environ["GEMINI_API_KEY"] = line.split("=", 1)[1].strip().strip('"\'')
                break

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com"

if GEMINI_API_KEY:
    print(f"Gemini initialized: {GEMINI_MODEL}")
else:
    print("Gemini not available: GEMINI_API_KEY not set")

client = None
assistant = None
assistant_file = None

try:
    from openai import OpenAI
    if os.environ.get('OPENAI_API_KEY'):
        client = OpenAI()
        print("OpenAI client initialized")
except Exception as e:
    print(f"OpenAI not available: {e}")

DATA_FILE = 'data/patients_50k.json'
if not os.path.exists(DATA_FILE):
    DATA_FILE = 'data/patients_final.json'

with open(DATA_FILE) as f:
    PATIENTS = json.load(f)
    PATIENTS_BY_ID = {p['id']: p for p in PATIENTS}
    print(f"Loaded {len(PATIENTS)} patients from {DATA_FILE}")


def build_patient_summary(patient, events_up_to=None):
    """Build clinical summary from events up to a given point."""
    events = patient['events']
    if events_up_to is not None:
        events = events[:events_up_to]

    diagnoses = [e for e in events if e['type'] == 'diagnosis']
    reports = [e for e in events if e['type'] == 'report']
    hospital = [e for e in events if e['type'] == 'hospital']

    summary = f"""Patient: {patient['id']}
Age: {patient['age']} | Sex: {patient['sex']}
BP: {patient['sbp']}/{patient['dbp']} mmHg | Smoker: {'Yes' if patient['smoking'] else 'No'}

CONDITIONS:
{chr(10).join(f"- {d['detail']} (diagnosed {d['date']})" for d in diagnoses) or "None recorded"}

RECENT INVESTIGATIONS:
{chr(10).join(f"- {r['title']}: {r['detail']} ({r['date']})" for r in reports[-5:]) or "None"}

HOSPITAL ATTENDANCES:
{chr(10).join(f"- {h['title']}: {h['detail']} ({h['date']})" for h in hospital) or "None"}
"""
    return summary


@app.route('/')
def index():
    return send_from_directory('.', 'strokeguard.html')


@app.route('/<path:path>')
def static_files(path):
    return send_from_directory('.', path)


@app.route('/api/patients')
def get_patients():
    limit = request.args.get('limit', 500, type=int)
    use_sim = request.args.get('sim', 'false').lower() == 'true'

    if use_sim:
        # Fetch from live simulation
        sim_patients = get_sim_all_patients()
        return jsonify(sim_patients[:limit])

    # Fallback to local data
    sorted_patients = sorted(PATIENTS, key=lambda p: p.get('stroke_risk', 0), reverse=True)
    return jsonify(sorted_patients[:limit])


@app.route('/api/sim/patients')
def get_sim_patients():
    """Get patients directly from live NHS-SIM simulation."""
    limit = request.args.get('limit', 100, type=int)
    sim_patients = get_sim_all_patients()
    return jsonify({"source": "nhs-sim", "world": NHS_SIM_WORLD, "patients": sim_patients[:limit]})


@app.route('/api/sim/patient/<patient_id>')
def get_sim_patient(patient_id):
    """Get full patient record from live NHS-SIM simulation."""
    patient_id = patient_id.upper()
    if not patient_id.startswith("SIM-"):
        patient_id = f"SIM-{patient_id.replace('P', '').zfill(6)}"

    data = get_sim_patient_ehr(patient_id)
    if not data:
        return jsonify({"error": "Patient not found in simulation"}), 404

    return jsonify({
        "source": "nhs-sim",
        "patient_id": patient_id,
        "ehr": data["ehr"],
        "resources": data["resources"],
        "sim_time": data["sim_time"]
    })


@app.route('/api/sim/resources')
def get_sim_resources():
    """Get all active resources from simulation."""
    site = request.args.get('site', 'gp')
    limit = request.args.get('limit', 100, type=int)

    data = sim_fetch(f"/api/sites/{site}/view", {"limit": limit})
    if not data:
        return jsonify({"error": "Failed to fetch from simulation"}), 500

    return jsonify({
        "source": "nhs-sim",
        "site": site,
        "now": data.get("now"),
        "paused": data.get("paused"),
        "population": data.get("population"),
        "resources": data.get("resources", [])
    })


@app.route('/api/sim/clock')
def get_sim_clock():
    """Get simulation clock status."""
    data = sim_fetch("/api/clock")
    if not data:
        return jsonify({"error": "Failed to fetch clock"}), 500
    return jsonify(data)


@app.route('/api/patient/<patient_id>')
def get_patient(patient_id):
    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return jsonify({'error': 'Patient not found'}), 404
    return jsonify(patient)


@app.route('/api/analyze', methods=['POST'])
def analyze_patient():
    """Get LLM analysis for a patient at a specific point in time."""
    data = request.json
    patient_id = data.get('patient_id')
    event_index = data.get('event_index')  # None for current

    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return jsonify({'error': 'Patient not found'}), 404

    summary = build_patient_summary(patient, event_index)

    prompt = f"""You are a UK NHS clinical decision support AI. Analyze this patient's stroke risk.

{summary}

Based on this clinical picture, provide:
1. RISK ASSESSMENT: Brief stroke risk assessment (2-3 sentences)
2. KEY FACTORS: List the 3 most important risk factors
3. RECOMMENDATIONS: 2-3 specific clinical actions to reduce stroke risk
4. URGENCY: Rate as LOW/MODERATE/HIGH/URGENT

Be specific and actionable. Reference NICE guidelines where relevant. Keep response under 200 words."""

    if not client:
        return jsonify({'error': 'Set OPENAI_API_KEY environment variable to enable LLM analysis'}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.3
        )
        analysis = response.choices[0].message.content
        return jsonify({
            'analysis': analysis,
            'patient_id': patient_id,
            'event_index': event_index
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/compare', methods=['POST'])
def compare_timepoints():
    """Compare risk between two time points for a patient."""
    data = request.json
    patient_id = data.get('patient_id')
    early_index = data.get('early_index', 0)
    late_index = data.get('late_index')

    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return jsonify({'error': 'Patient not found'}), 404

    early_summary = build_patient_summary(patient, early_index)
    late_summary = build_patient_summary(patient, late_index)

    prompt = f"""Compare stroke risk between two time points for this patient.

EARLIER POINT:
{early_summary}

LATER POINT:
{late_summary}

What changed? How did the risk evolve? What interventions could have altered the trajectory?
Be specific about which events drove the risk change. Keep response under 150 words."""

    if not client:
        return jsonify({'error': 'Set OPENAI_API_KEY environment variable to enable LLM analysis'}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.3
        )
        comparison = response.choices[0].message.content
        return jsonify({
            'comparison': comparison,
            'patient_id': patient_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/intervention', methods=['POST'])
def suggest_intervention():
    """Get specific intervention suggestions for a patient."""
    data = request.json
    patient_id = data.get('patient_id')

    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return jsonify({'error': 'Patient not found'}), 404

    summary = build_patient_summary(patient)

    prompt = f"""You are an NHS stroke prevention specialist. This patient has elevated stroke risk.

{summary}

Provide a specific, actionable intervention plan:

1. IMMEDIATE (this week): What should the GP do right now?
2. SHORT-TERM (1 month): What investigations or referrals?
3. LONG-TERM (ongoing): What monitoring and lifestyle changes?

Reference specific medications (e.g., DOACs for AFib, statins, antihypertensives) and NICE guidance where appropriate. Be concrete, not generic."""

    if not client:
        return jsonify({'error': 'Set OPENAI_API_KEY environment variable to enable LLM analysis'}), 400

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.3
        )
        plan = response.choices[0].message.content
        return jsonify({
            'intervention_plan': plan,
            'patient_id': patient_id
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


TERMINAL_SYSTEM = """You are StrokeGuard Terminal, an NHS clinical analytics agent with Code Interpreter.

You have access to patients_final.json containing synthetic patient records with:
- id, age, sex, smoking, sbp/dbp (blood pressure)
- conditions: list of diagnoses with dates
- events: timeline of diagnoses, reports (ECG, Echo, CT, bloods), GP visits, hospital attendances

You can run Python to:
- Query/filter patients by any criteria
- Calculate population statistics
- Identify high-risk cohorts needing intervention
- Analyze patterns (e.g., "patients with AFib but no anticoagulation")
- Generate charts and visualizations

Be concise. Use patient IDs. Reference NICE guidelines. You're a clinical decision support terminal."""

threads = {}


def get_or_create_assistant():
    """Get or create the terminal assistant."""
    global assistant, assistant_file
    if assistant:
        return assistant, assistant_file

    if not client:
        return None, None

    for a in client.beta.assistants.list(limit=20).data:
        if a.name == "StrokeGuard Terminal":
            assistant = a
            break
    else:
        assistant = client.beta.assistants.create(
            name="StrokeGuard Terminal",
            instructions=TERMINAL_SYSTEM,
            model="gpt-4o",
            tools=[{"type": "code_interpreter"}]
        )

    with open("data/patients_final.json", "rb") as f:
        assistant_file = client.files.create(file=f, purpose="assistants")

    return assistant, assistant_file


@app.route('/api/terminal', methods=['POST'])
def terminal_chat():
    """Terminal agent endpoint - streams responses."""
    data = request.json
    message = data.get('message', '')
    session_id = data.get('session_id', 'default')

    if not client:
        return jsonify({'error': 'OPENAI_API_KEY not set'}), 400

    asst, file = get_or_create_assistant()
    if not asst:
        return jsonify({'error': 'Could not create assistant'}), 500

    if session_id not in threads:
        threads[session_id] = client.beta.threads.create()

    thread = threads[session_id]

    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=message,
        attachments=[{"file_id": file.id, "tools": [{"type": "code_interpreter"}]}]
    )

    run = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=asst.id
    )

    while run.status in ("queued", "in_progress"):
        time.sleep(0.3)
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

    if run.status == "completed":
        messages = client.beta.threads.messages.list(thread_id=thread.id, limit=1)
        for msg in messages.data:
            if msg.role == "assistant":
                response_parts = []
                for block in msg.content:
                    if block.type == "text":
                        response_parts.append(block.text.value)
                    elif block.type == "image_file":
                        response_parts.append(f"[Generated visualization: {block.image_file.file_id}]")
                return jsonify({'response': '\n'.join(response_parts)})
        return jsonify({'response': 'No response generated'})
    else:
        return jsonify({'error': f'Run failed: {run.status}', 'details': str(run.last_error)}), 500


@app.route('/api/terminal/clear', methods=['POST'])
def clear_terminal():
    """Clear terminal session."""
    session_id = request.json.get('session_id', 'default')
    if session_id in threads:
        del threads[session_id]
    return jsonify({'status': 'cleared'})


# ============================================================================
# PATIENT EXPLORER CODEX (OpenAI Agents SDK with Gemini)
# ============================================================================

from agents import Agent, Runner, function_tool, set_default_openai_client
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI
from typing import Optional
import asyncio

# Configure Gemini as the provider via OpenAI-compatible endpoint
gemini_client = AsyncOpenAI(
    api_key=GEMINI_API_KEY,
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)

GEMINI_MODEL_ID = "gemini-3.6-flash"  # Gemini model via OpenAI-compatible API

# Define tools using OpenAI Agents SDK decorator
@function_tool
def search_patients(
    condition: Optional[str] = None,
    min_risk: Optional[float] = None,
    max_risk: Optional[float] = None,
    min_age: Optional[int] = None,
    max_age: Optional[int] = None,
    smoking: Optional[bool] = None,
    limit: int = 10
) -> str:
    """Search patients by criteria. Returns matching patient summaries."""
    results = []
    for p in PATIENTS:
        if condition:
            cond = condition.lower()
            if not any(cond in c["term"].lower() for c in p.get("conditions", [])):
                continue
        if min_risk is not None and p.get("stroke_risk", 0) < min_risk:
            continue
        if max_risk is not None and p.get("stroke_risk", 0) > max_risk:
            continue
        if min_age is not None and p["age"] < min_age:
            continue
        if max_age is not None and p["age"] > max_age:
            continue
        if smoking is not None and p["smoking"] != smoking:
            continue

        results.append({
            "id": p["id"],
            "age": p["age"],
            "sex": p["sex"],
            "risk": round(p.get("stroke_risk", 0) * 100, 1),
            "conditions": [c["term"] for c in p.get("conditions", [])],
            "bp": f"{p['sbp']}/{p['dbp']}"
        })

    results.sort(key=lambda x: x["risk"], reverse=True)
    return json.dumps({"patients": results[:limit], "total_matches": len(results)})


@function_tool
def get_patient_details(patient_id: str) -> str:
    """Get full clinical record for a specific patient."""
    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return json.dumps({"error": "Patient not found"})
    return json.dumps({
        "id": patient["id"],
        "demographics": {
            "age": patient["age"],
            "sex": patient["sex"],
            "smoking": patient["smoking"],
            "bp": f"{patient['sbp']}/{patient['dbp']}"
        },
        "stroke_risk": round(patient.get("stroke_risk", 0) * 100, 1),
        "will_stroke": patient.get("will_stroke", False),
        "conditions": [{"term": c["term"], "date": c["date"]} for c in patient.get("conditions", [])],
        "recent_events": patient.get("events", [])[-10:],
        "total_events": len(patient.get("events", []))
    }, default=str)


@function_tool
def analyze_patient_risk(patient_id: str) -> str:
    """Calculate detailed risk breakdown for a patient."""
    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return json.dumps({"error": "Patient not found"})

    factors = []
    conditions = [c["term"].lower() for c in patient.get("conditions", [])]

    if any("atrial" in c or "fibrillation" in c for c in conditions):
        factors.append({"factor": "Atrial Fibrillation", "multiplier": 5.0, "severity": "critical"})
    if any("stroke" in c for c in conditions):
        factors.append({"factor": "Previous Stroke", "multiplier": 3.0, "severity": "critical"})
    if any("tia" in c for c in conditions):
        factors.append({"factor": "Previous TIA", "multiplier": 2.5, "severity": "high"})
    if any("hypertension" in c for c in conditions):
        factors.append({"factor": "Hypertension", "multiplier": 2.0, "severity": "moderate"})
    if any("diabetes" in c for c in conditions):
        factors.append({"factor": "Diabetes", "multiplier": 1.5, "severity": "moderate"})
    if patient["smoking"]:
        factors.append({"factor": "Current Smoker", "multiplier": 1.4, "severity": "moderate"})
    if patient["sbp"] > 140:
        factors.append({"factor": "Elevated BP", "multiplier": 1.2, "severity": "low"})
    if patient["age"] > 75:
        factors.append({"factor": "Age >75", "multiplier": 1.3, "severity": "moderate"})

    return json.dumps({
        "patient_id": patient["id"],
        "overall_risk": round(patient.get("stroke_risk", 0) * 100, 1),
        "risk_factors": factors,
        "recommendation": "Urgent review needed" if patient.get("stroke_risk", 0) > 0.2 else "Standard monitoring"
    })


@function_tool
def compare_patients(patient_id_1: str, patient_id_2: str) -> str:
    """Compare two patients side by side."""
    p1 = PATIENTS_BY_ID.get(patient_id_1)
    p2 = PATIENTS_BY_ID.get(patient_id_2)
    if not p1 or not p2:
        return json.dumps({"error": "One or both patients not found"})

    return json.dumps({
        "comparison": {
            "patient_1": {
                "id": p1["id"], "age": p1["age"],
                "risk": round(p1.get("stroke_risk", 0) * 100, 1),
                "conditions": [c["term"] for c in p1.get("conditions", [])]
            },
            "patient_2": {
                "id": p2["id"], "age": p2["age"],
                "risk": round(p2.get("stroke_risk", 0) * 100, 1),
                "conditions": [c["term"] for c in p2.get("conditions", [])]
            }
        }
    })


@function_tool
def get_population_stats(metric: str) -> str:
    """Get statistics across the patient population. Metric: risk_distribution, condition_prevalence, age_distribution, bp_distribution."""
    if metric == "risk_distribution":
        buckets = {"<5%": 0, "5-10%": 0, "10-15%": 0, "15-20%": 0, ">20%": 0}
        for p in PATIENTS:
            r = p.get("stroke_risk", 0)
            if r < 0.05: buckets["<5%"] += 1
            elif r < 0.10: buckets["5-10%"] += 1
            elif r < 0.15: buckets["10-15%"] += 1
            elif r < 0.20: buckets["15-20%"] += 1
            else: buckets[">20%"] += 1
        return json.dumps({"metric": "risk_distribution", "data": buckets})

    elif metric == "condition_prevalence":
        counts = {}
        for p in PATIENTS:
            for c in p.get("conditions", []):
                counts[c["term"]] = counts.get(c["term"], 0) + 1
        sorted_conditions = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
        return json.dumps({"metric": "condition_prevalence", "data": dict(sorted_conditions)})

    elif metric == "age_distribution":
        buckets = {"<50": 0, "50-59": 0, "60-69": 0, "70-79": 0, "80+": 0}
        for p in PATIENTS:
            age = p["age"]
            if age < 50: buckets["<50"] += 1
            elif age < 60: buckets["50-59"] += 1
            elif age < 70: buckets["60-69"] += 1
            elif age < 80: buckets["70-79"] += 1
            else: buckets["80+"] += 1
        return json.dumps({"metric": "age_distribution", "data": buckets})

    elif metric == "bp_distribution":
        data = {
            "Normal": sum(1 for p in PATIENTS if p["sbp"] < 120),
            "Elevated": sum(1 for p in PATIENTS if 120 <= p["sbp"] < 130),
            "High Stage 1": sum(1 for p in PATIENTS if 130 <= p["sbp"] < 140),
            "High Stage 2": sum(1 for p in PATIENTS if p["sbp"] >= 140)
        }
        return json.dumps({"metric": "bp_distribution", "data": data})

    return json.dumps({"error": "Unknown metric"})


@function_tool
def find_similar_patients(patient_id: str, limit: int = 5) -> str:
    """Find patients similar to a given patient."""
    target = PATIENTS_BY_ID.get(patient_id)
    if not target:
        return json.dumps({"error": "Patient not found"})

    target_conditions = set(c["term"] for c in target.get("conditions", []))
    similar = []

    for p in PATIENTS:
        if p["id"] == target["id"]:
            continue
        p_conditions = set(c["term"] for c in p.get("conditions", []))
        overlap = len(target_conditions & p_conditions)
        age_diff = abs(p["age"] - target["age"])
        score = overlap * 10 - age_diff
        similar.append((p, score, p_conditions & target_conditions))

    similar.sort(key=lambda x: x[1], reverse=True)

    return json.dumps({
        "reference_patient": target["id"],
        "similar_patients": [
            {"id": p["id"], "age": p["age"], "risk": round(p.get("stroke_risk", 0) * 100, 1),
             "shared_conditions": list(shared)}
            for p, _, shared in similar[:limit]
        ]
    })


@function_tool
def generate_referral(patient_id: str, urgency: str, reason: str) -> str:
    """Generate a referral letter for a patient to stroke clinic. Urgency: routine, urgent, 2ww, emergency."""
    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return json.dumps({"error": "Patient not found"})

    os.makedirs("output/referrals", exist_ok=True)
    letter = f"""
STROKE/TIA CLINIC REFERRAL
==========================
Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}
Urgency: {urgency.upper()}

PATIENT: {patient['id']}
Age: {patient['age']} | Sex: {patient['sex']}
BP: {patient['sbp']}/{patient['dbp']} mmHg

CONDITIONS:
{chr(10).join('- ' + c['term'] for c in patient.get('conditions', []))}

REASON FOR REFERRAL:
{reason}

5-YEAR STROKE RISK: {round(patient.get('stroke_risk', 0) * 100, 1)}%

---
Generated by StrokeGuard AI
"""
    filepath = f"output/referrals/{patient['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(filepath, "w") as f:
        f.write(letter)

    return json.dumps({"status": "generated", "patient_id": patient["id"], "urgency": urgency, "file": filepath})


@function_tool
def flag_for_review(patient_id: str, priority: str, reason: str) -> str:
    """Flag a patient for clinical review. Priority: routine, soon, urgent, immediate."""
    patient = PATIENTS_BY_ID.get(patient_id)
    if not patient:
        return json.dumps({"error": "Patient not found"})

    return json.dumps({
        "status": "flagged",
        "patient_id": patient_id,
        "priority": priority,
        "reason": reason,
        "timestamp": datetime.now().isoformat()
    })


# Create the Codex Agent
CODEX_INSTRUCTIONS = """You are the StrokeGuard Patient Explorer Codex — an intelligent clinical assistant for NHS stroke prevention.

You help clinicians explore patient data, identify high-risk cases, and take action. You have tools to:
- Search and filter patients by conditions, risk, age, etc.
- Get detailed patient records
- Analyze risk factors
- Compare patients
- View population statistics
- Generate referral letters
- Flag patients for review

Be helpful, concise, and clinically accurate. When you find concerning cases, proactively suggest actions. Reference NICE guidelines for stroke prevention where relevant.

Always explain your reasoning. If a patient has AFib without anticoagulation, flag it. If BP is uncontrolled, recommend review.

You're an expert clinical decision support system. Help the clinician work efficiently.

IMPORTANT: You are an autonomous agent. Use your tools to gather information, analyze it, and take actions. Chain multiple tool calls as needed to fully answer queries."""

# Create Gemini model for the agent
gemini_model = OpenAIChatCompletionsModel(
    model=GEMINI_MODEL_ID,
    openai_client=gemini_client
)

codex_agent = Agent(
    name="StrokeGuard Codex",
    instructions=CODEX_INSTRUCTIONS,
    model=gemini_model,
    tools=[
        search_patients,
        get_patient_details,
        analyze_patient_risk,
        compare_patients,
        get_population_stats,
        find_similar_patients,
        generate_referral,
        flag_for_review
    ]
)


@app.route('/api/codex', methods=['POST'])
def codex_chat():
    """Patient Explorer Codex endpoint using OpenAI Agents SDK with Gemini."""
    if not GEMINI_API_KEY:
        return jsonify({'error': 'GEMINI_API_KEY not set'}), 400

    data = request.json
    message = data.get('message', '')

    try:
        # Run the agent synchronously
        result = Runner.run_sync(codex_agent, message)

        # Collect tool call steps from the run history
        steps = []

        # Extract from new_items which contains all the steps
        if hasattr(result, 'new_items'):
            for item in result.new_items:
                item_type = getattr(item, 'type', None)

                # Tool call request
                if item_type == 'function_call_item':
                    call_id = getattr(item, 'call_id', None)
                    name = getattr(item, 'name', 'unknown')
                    args_raw = getattr(item, 'arguments', '{}')
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except:
                        args = {"raw": str(args_raw)}
                    steps.append({
                        "tool": name,
                        "args": args,
                        "call_id": call_id,
                        "status": "calling"
                    })

                # Tool call result
                elif item_type == 'function_call_output_item':
                    call_id = getattr(item, 'call_id', None)
                    output = getattr(item, 'output', '')
                    # Find matching call and update it
                    for step in steps:
                        if step.get('call_id') == call_id:
                            try:
                                step['result'] = json.loads(output) if isinstance(output, str) else output
                            except:
                                step['result'] = output
                            step['status'] = 'completed'
                            break

        # Clean up call_ids and status from response
        for step in steps:
            step.pop('call_id', None)
            step.pop('status', None)

        return jsonify({
            "response": result.final_output,
            "steps": steps
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/codex/clear', methods=['POST'])
def clear_codex():
    """Clear codex session (no-op for stateless agent)."""
    return jsonify({'status': 'cleared'})


# ============================================================================
# VAPI VOICE AGENT WEBHOOKS
# ============================================================================

CALL_CONTEXT = {}
TOOL_EVENTS = []  # SSE event buffer for real-time updates

@app.route('/api/events')
def sse_events():
    """Server-Sent Events endpoint for real-time tool call notifications."""
    def generate():
        last_idx = len(TOOL_EVENTS)
        yield f"data: {json.dumps({'type': 'connected'})}\n\n"
        while True:
            if len(TOOL_EVENTS) > last_idx:
                for evt in TOOL_EVENTS[last_idx:]:
                    yield f"data: {json.dumps(evt)}\n\n"
                last_idx = len(TOOL_EVENTS)
            time.sleep(0.3)
    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.route('/api/vapi/tool', methods=['POST'])
def vapi_tool_webhook():
    """Vapi server-side tool execution webhook - comprehensive NHS clinical actions."""
    data = request.json
    message = data.get('message', {})

    if message.get('type') != 'function-call':
        return jsonify({"result": "ok"})

    func = message.get('functionCall', {})
    name = func.get('name')
    params = func.get('parameters', {})
    call_id = data.get('call', {}).get('id', 'unknown')

    patient_id = CALL_CONTEXT.get(call_id, {}).get('patient_id')
    if not patient_id:
        for p_id in PATIENTS_BY_ID.keys():
            if p_id in str(data):
                patient_id = p_id
                CALL_CONTEXT[call_id] = {'patient_id': patient_id}
                break

    patient = PATIENTS_BY_ID.get(patient_id) if patient_id else None
    result = {"error": "Patient context not found"}

    if name == 'get_full_patient_record' and patient:
        events = patient.get('events', [])
        diagnoses = [e for e in events if e['type'] == 'diagnosis']
        reports = [e for e in events if e['type'] == 'report']
        hospital = [e for e in events if e['type'] == 'hospital']
        medications = [e for e in events if e['type'] == 'medication']

        result = {
            "patient_id": patient["id"],
            "demographics": {"age": patient["age"], "sex": patient["sex"]},
            "vitals": {"bp": f"{patient['sbp']}/{patient['dbp']}", "smoking": patient["smoking"]},
            "stroke_risk": f"{round(patient.get('stroke_risk', 0) * 100, 1)}%",
            "conditions": [{"condition": c["term"], "diagnosed": c.get("date", "unknown")} for c in patient.get("conditions", [])],
            "active_medications": [{"drug": m.get("detail", "unknown"), "started": m.get("date")} for m in medications[-5:]],
            "recent_investigations": [{"test": r["title"], "result": r["detail"], "date": r["date"]} for r in reports[-5:]],
            "hospital_admissions": [{"reason": h["title"], "details": h["detail"], "date": h["date"]} for h in hospital[-3:]],
            "total_events": len(events)
        }

    elif name == 'get_risk_explanation' and patient:
        factors = []
        conditions = [c["term"].lower() for c in patient.get("conditions", [])]
        risk = patient.get("stroke_risk", 0)

        if any("atrial" in c or "fibrillation" in c for c in conditions):
            factors.append({"factor": "Atrial Fibrillation", "impact": "HIGH", "explanation": "Irregular heartbeat can cause blood clots that travel to the brain"})
        if any("stroke" in c or "tia" in c for c in conditions):
            factors.append({"factor": "Previous stroke/TIA", "impact": "HIGH", "explanation": "History of stroke significantly increases future risk"})
        if any("hypertension" in c for c in conditions):
            factors.append({"factor": "Hypertension", "impact": "MODERATE", "explanation": "High blood pressure damages blood vessel walls"})
        if any("diabetes" in c for c in conditions):
            factors.append({"factor": "Diabetes", "impact": "MODERATE", "explanation": "Affects blood vessel health and increases clotting risk"})
        if patient["smoking"]:
            factors.append({"factor": "Smoking", "impact": "MODERATE", "explanation": "Damages arteries and increases clot formation"})
        if patient["sbp"] > 140:
            factors.append({"factor": "Elevated BP reading", "impact": "MODERATE", "explanation": f"Current BP {patient['sbp']}/{patient['dbp']} is above target"})
        if patient["age"] > 75:
            factors.append({"factor": "Age over 75", "impact": "MODERATE", "explanation": "Stroke risk increases with age"})

        result = {
            "overall_risk": f"{round(risk * 100, 1)}%",
            "risk_category": "HIGH" if risk > 0.15 else "MODERATE" if risk > 0.08 else "LOW",
            "risk_factors": factors,
            "recommendation": "Urgent GP review recommended" if risk > 0.15 else "Continue current management"
        }

    elif name == 'check_medication_compliance' and patient:
        events = patient.get('events', [])
        meds = [e for e in events if e['type'] == 'medication']
        result = {
            "current_medications": [{"name": m.get("detail", "unknown"), "started": m.get("date")} for m in meds[-8:]],
            "last_prescription_collected": "3 days ago",
            "compliance_status": "Good - regular collections",
            "notes": "Patient collecting medications regularly from pharmacy"
        }

    elif name == 'schedule_gp_appointment':
        urgency = params.get('urgency', 'routine')
        reason = params.get('reason', 'Review')
        os.makedirs("output/appointments", exist_ok=True)
        filepath = f"output/appointments/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w") as f:
            json.dump({"patient_id": patient_id, "type": "GP", "urgency": urgency, "reason": reason, "booked": datetime.now().isoformat()}, f, indent=2)

        # Push to NHS-SIM if patient is from simulation
        if patient_id and patient_id.startswith("SIM-"):
            sim_action = {
                "kind": "schedule_appointment",
                "patientId": patient_id,
                "data": {"urgency": urgency, "reason": reason, "type": "GP review"}
            }
            sim_result = sim_post_action("gp", sim_action)
            print(f"[SIM] GP appointment action: {sim_result}")

        timeframe = {"same-day": "today", "urgent": "within 48 hours", "routine": "within 2 weeks"}
        result = {
            "status": "BOOKED",
            "appointment_type": "GP appointment",
            "urgency": urgency,
            "timeframe": timeframe.get(urgency, "within 2 weeks"),
            "reason": reason,
            "confirmation": f"Appointment booked for {patient_id}. Patient will receive SMS confirmation.",
            "sim_linked": patient_id.startswith("SIM-") if patient_id else False
        }

    elif name == 'request_medication_review':
        concern = params.get('concern', '')
        priority = params.get('priority', 'routine')
        os.makedirs("output/med_reviews", exist_ok=True)
        filepath = f"output/med_reviews/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w") as f:
            json.dump({"patient_id": patient_id, "concern": concern, "priority": priority, "requested": datetime.now().isoformat()}, f, indent=2)

        result = {
            "status": "REQUESTED",
            "review_type": "Medication review",
            "concern_logged": concern,
            "priority": priority,
            "expected_response": "Pharmacist will contact within 48 hours" if priority == "routine" else "Pharmacist will contact within 24 hours"
        }

    elif name == 'order_investigation':
        test_type = params.get('test_type', '')
        indication = params.get('indication', '')
        os.makedirs("output/investigations", exist_ok=True)
        filepath = f"output/investigations/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w") as f:
            json.dump({"patient_id": patient_id, "test": test_type, "indication": indication, "ordered": datetime.now().isoformat()}, f, indent=2)

        test_names = {
            "bloods_fbc": "Full Blood Count", "bloods_lipids": "Lipid Profile", "bloods_hba1c": "HbA1c",
            "bloods_renal": "Renal Function", "bloods_coag": "Coagulation Screen", "ecg": "12-lead ECG",
            "echo": "Echocardiogram", "carotid_doppler": "Carotid Doppler Ultrasound",
            "24hr_bp": "24-hour Blood Pressure Monitor", "holter": "Holter Monitor (24hr ECG)"
        }

        result = {
            "status": "ORDERED",
            "investigation": test_names.get(test_type, test_type),
            "indication": indication,
            "instructions": "Blood forms sent to patient. Attend phlebotomy Mon-Fri 8am-4pm." if "bloods" in test_type else "Appointment letter will be sent within 5 working days."
        }

    elif name == 'refer_to_specialist':
        specialty = params.get('specialty', '')
        urgency = params.get('urgency', 'routine')
        reason = params.get('reason', '')
        os.makedirs("output/referrals", exist_ok=True)
        filepath = f"output/referrals/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w") as f:
            json.dump({"patient_id": patient_id, "specialty": specialty, "urgency": urgency, "reason": reason, "referred": datetime.now().isoformat()}, f, indent=2)

        timeframes = {"routine": "within 18 weeks", "urgent": "within 2 weeks", "2ww": "within 2 weeks (cancer pathway)"}
        result = {
            "status": "REFERRED",
            "specialty": specialty.replace("_", " ").title(),
            "urgency": urgency,
            "reason": reason,
            "expected_appointment": timeframes.get(urgency, "TBC"),
            "confirmation": f"Referral submitted to {specialty}. Patient will receive appointment letter."
        }

    elif name == 'add_clinical_note':
        note = params.get('note', '')
        category = params.get('category', 'welfare_check')
        os.makedirs("output/notes", exist_ok=True)
        filepath = f"output/notes/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w") as f:
            json.dump({"patient_id": patient_id, "note": note, "category": category, "author": "StrokeGuard AI Agent", "timestamp": datetime.now().isoformat()}, f, indent=2)

        result = {
            "status": "SAVED",
            "note_added": note[:100] + "..." if len(note) > 100 else note,
            "category": category,
            "visible_to": "All clinical staff"
        }

    elif name == 'flag_urgent_review':
        reason = params.get('reason', '')
        symptoms = params.get('symptoms', '')
        os.makedirs("output/urgent_flags", exist_ok=True)
        filepath = f"output/urgent_flags/{patient_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filepath, "w") as f:
            json.dump({"patient_id": patient_id, "reason": reason, "symptoms": symptoms, "flagged": datetime.now().isoformat(), "status": "PENDING"}, f, indent=2)

        result = {
            "status": "FLAGGED - URGENT",
            "action": "Duty GP notified immediately",
            "reason": reason,
            "symptoms": symptoms,
            "expected_callback": "Within 2 hours",
            "alert": "Patient flagged for urgent same-day review"
        }

    elif name == 'check_recent_results' and patient:
        events = patient.get('events', [])
        reports = [e for e in events if e['type'] == 'report'][-10:]
        result = {
            "recent_results": [{"test": r["title"], "result": r["detail"], "date": r["date"]} for r in reports],
            "outstanding_tests": "None",
            "last_updated": reports[-1]["date"] if reports else "No recent results"
        }

    print(f"[VAPI TOOL] {name} -> {json.dumps(result)[:200]}")

    # Emit SSE event for real-time dashboard updates
    TOOL_EVENTS.append({
        "type": "tool_call",
        "tool": name,
        "patient_id": patient_id,
        "result": result,
        "timestamp": datetime.now().isoformat()
    })
    # Keep buffer bounded
    if len(TOOL_EVENTS) > 100:
        TOOL_EVENTS.pop(0)

    return jsonify({"result": json.dumps(result)})


@app.route('/api/vapi/config/<patient_id>')
def vapi_assistant_config(patient_id):
    """Generate Vapi assistant config for a specific patient."""
    patient = PATIENTS_BY_ID.get(patient_id.upper())
    if not patient:
        return jsonify({"error": "Patient not found"}), 404

    conditions = ", ".join(c["term"] for c in patient.get("conditions", [])) or "None"
    risk_pct = round(patient.get("stroke_risk", 0) * 100, 1)

    config = {
        "name": f"StrokeGuard Support - {patient_id}",
        "firstMessage": f"Hello! This is the StrokeGuard patient support line. I'm calling to check in on your health. Am I speaking with the patient registered as {patient_id}?",
        "context": f"""You are calling patient {patient_id} for a health check-in.

PATIENT PROFILE:
- Age: {patient['age']}, Sex: {patient['sex']}
- Blood Pressure: {patient['sbp']}/{patient['dbp']} mmHg
- Stroke Risk: {risk_pct}%
- Conditions: {conditions}
- Smoker: {'Yes' if patient['smoking'] else 'No'}

CALL OBJECTIVES:
1. Verify patient identity
2. Check on their wellbeing
3. Explain risk factors in simple terms
4. Answer questions about their health
5. Offer to schedule follow-ups if needed

GUIDELINES:
- Be warm, empathetic, and reassuring
- Use plain English, avoid medical jargon
- Keep responses conversational (2-3 sentences)
- EMERGENCY: If they mention chest pain, sudden weakness, facial drooping, or speech problems - tell them to call 999 IMMEDIATELY and end the call""",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup_patient",
                    "description": "Look up a patient's medical record",
                    "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_risk_explanation",
                    "description": "Get plain-language explanation of stroke risk factors",
                    "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}}, "required": ["patient_id"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "schedule_followup",
                    "description": "Schedule a follow-up appointment",
                    "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}, "urgency": {"type": "string", "enum": ["routine", "soon", "urgent"]}, "reason": {"type": "string"}}, "required": ["patient_id", "urgency", "reason"]}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "request_medication_review",
                    "description": "Request a medication review",
                    "parameters": {"type": "object", "properties": {"patient_id": {"type": "string"}, "concern": {"type": "string"}}, "required": ["patient_id", "concern"]}
                }
            }
        ],
        "serverUrl": request.url_root.rstrip('/') + "/api/vapi/tool"
    }

    return jsonify(config)


@app.route('/api/chat', methods=['POST'])
def chat():
    """Chat endpoint for voice agent - uses Gemini."""
    data = request.json
    messages = data.get('messages', [])

    if not GEMINI_API_KEY:
        return jsonify({'error': 'GEMINI_API_KEY not set'}), 500

    # Convert messages to Gemini format
    gemini_contents = []
    system_instruction = None

    for msg in messages:
        role = msg.get('role')
        content = msg.get('content', '')

        if role == 'system':
            system_instruction = content
        elif role == 'user':
            gemini_contents.append({"role": "user", "parts": [{"text": content}]})
        elif role == 'assistant':
            gemini_contents.append({"role": "model", "parts": [{"text": content}]})

    try:
        url = f"{GEMINI_BASE_URL}/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
        payload = {"contents": gemini_contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        payload["generationConfig"] = {"maxOutputTokens": 200, "temperature": 0.7}

        resp = requests.post(url, json=payload, timeout=30)
        result = resp.json()

        if 'candidates' in result and result['candidates']:
            text = result['candidates'][0]['content']['parts'][0]['text']
            return jsonify({'response': text})
        else:
            return jsonify({'error': 'No response from model', 'details': result}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# OUTREACH SYSTEM - Trigger Detection & Call Queue
# ============================================================================

CALL_QUEUE = []
CALL_HISTORY = []

@app.route('/api/triggers')
def get_triggers():
    """Find patients with active triggers (high stroke risk)."""
    threshold = float(request.args.get('threshold', 0.15))
    limit = int(request.args.get('limit', 100))

    triggers = []
    for p in PATIENTS:
        risk = p.get('stroke_risk', 0)
        if risk > threshold:
            triggers.append({
                'patient_id': p['id'],
                'trigger': 'HIGH_STROKE_RISK',
                'urgency': 'CRITICAL' if risk > 0.30 else 'HIGH' if risk > 0.20 else 'MEDIUM',
                'risk_percent': round(risk * 100, 1),
                'age': p['age'],
                'sex': p['sex'],
                'conditions': [c['term'] for c in p.get('conditions', [])][:3],
                'bp': f"{p['sbp']}/{p['dbp']}"
            })

    triggers.sort(key=lambda t: -t['risk_percent'])
    return jsonify(triggers[:limit])


@app.route('/api/outreach/queue', methods=['GET'])
def get_outreach_queue():
    """Get pending calls in queue."""
    return jsonify(CALL_QUEUE)


@app.route('/api/outreach/queue', methods=['POST'])
def add_to_queue():
    """Add a patient to the call queue."""
    data = request.json
    patient_id = data.get('patient_id')

    if not PATIENTS_BY_ID.get(patient_id):
        return jsonify({'error': 'Patient not found'}), 404

    # Check if already in queue
    if any(c['patient_id'] == patient_id for c in CALL_QUEUE):
        return jsonify({'error': 'Already in queue'}), 400

    entry = {
        'patient_id': patient_id,
        'trigger': data.get('trigger', 'MANUAL'),
        'status': 'pending',
        'queued_at': datetime.now().isoformat()
    }
    CALL_QUEUE.append(entry)
    return jsonify(entry)


@app.route('/api/outreach/complete', methods=['POST'])
def complete_call():
    """Mark a call as completed and log outcome."""
    data = request.json
    patient_id = data.get('patient_id')
    outcome = data.get('outcome', 'completed')
    notes = data.get('notes', '')

    # Remove from queue
    global CALL_QUEUE
    CALL_QUEUE = [c for c in CALL_QUEUE if c['patient_id'] != patient_id]

    # Add to history
    CALL_HISTORY.append({
        'patient_id': patient_id,
        'outcome': outcome,
        'notes': notes,
        'completed_at': datetime.now().isoformat()
    })

    return jsonify({'status': 'logged'})


@app.route('/api/outreach/history')
def get_call_history():
    """Get call history."""
    return jsonify(CALL_HISTORY[-100:])


# ============================================================================
# WHATSAPP VOICE SERVER CONTROL
# ============================================================================

import subprocess
import signal

WHATSAPP_SERVER_PROCESS = None

@app.route('/api/whatsapp/status')
def whatsapp_status():
    """Check if WhatsApp voice server is running."""
    try:
        resp = requests.get('http://localhost:3001/status', timeout=2)
        return jsonify({'running': True, 'data': resp.json()})
    except:
        return jsonify({'running': False})


@app.route('/api/whatsapp/start', methods=['POST'])
def whatsapp_start():
    """Start the WhatsApp voice server."""
    global WHATSAPP_SERVER_PROCESS

    # Check if already running
    try:
        requests.get('http://localhost:3001/status', timeout=2)
        return jsonify({'status': 'already_running'})
    except:
        pass

    # Start the server
    try:
        WHATSAPP_SERVER_PROCESS = subprocess.Popen(
            ['node', 'whatsapp_voice_server_v2.mjs'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        # Give it time to start
        time.sleep(3)

        # Check if it started
        try:
            requests.get('http://localhost:3001/status', timeout=2)
            return jsonify({'status': 'started', 'pid': WHATSAPP_SERVER_PROCESS.pid})
        except:
            return jsonify({'status': 'failed', 'error': 'Server did not start'}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500


@app.route('/api/whatsapp/stop', methods=['POST'])
def whatsapp_stop():
    """Stop the WhatsApp voice server."""
    global WHATSAPP_SERVER_PROCESS

    # Kill our tracked process
    if WHATSAPP_SERVER_PROCESS:
        try:
            WHATSAPP_SERVER_PROCESS.terminate()
        except:
            pass
        WHATSAPP_SERVER_PROCESS = None

    # Also kill any other process on port 3001
    try:
        result = subprocess.run(['lsof', '-ti', ':3001'], capture_output=True, text=True)
        if result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except:
                    pass
    except:
        pass

    return jsonify({'status': 'stopped'})


if __name__ == '__main__':
    print("StrokeGuard running at http://localhost:8000")
    app.run(host='0.0.0.0', port=8000, debug=False)
