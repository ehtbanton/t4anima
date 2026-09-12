#!/usr/bin/env python3
"""
NHS-SIM Stroke Prevention Agent

Connects to the NHS-SIM API, pulls patient data, runs stroke risk prediction,
and takes preventive actions (referrals, tests, alerts).

Usage:
    python nhs_sim_agent.py --token <API_TOKEN> --model models/stroke_model.pkl
"""

import os
import json
import argparse
import requests
from datetime import datetime
from typing import List, Dict, Optional, Tuple

# =============================================================================
# NHS-SIM API CLIENT
# =============================================================================

class NHSSimClient:
    """Client for NHS-SIM API."""

    BASE_URL = "https://sim.animahacks.com/api"

    def __init__(self, token: str):
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    def get_team(self) -> dict:
        """Get team info."""
        r = requests.get(f"{self.BASE_URL}/team", headers=self.headers)
        return r.json()

    def get_clock(self) -> dict:
        """Get simulation clock and events."""
        r = requests.get(f"{self.BASE_URL}/clock", headers=self.headers)
        return r.json()

    def get_site_view(self, site: str) -> dict:
        """Get view for a site (gp, hospital, pharmacy, etc)."""
        r = requests.get(f"{self.BASE_URL}/sites/{site}/view", headers=self.headers)
        return r.json()

    def post_action(self, site: str, action: dict) -> dict:
        """Post an action to a site."""
        r = requests.post(
            f"{self.BASE_URL}/sites/{site}/actions",
            headers=self.headers,
            json=action
        )
        return r.json()

    def create_referral(self, patient_id: str, specialty: str, reason: str) -> dict:
        """Create a referral for a patient."""
        return self.post_action("gp", {
            "type": "create_referral",
            "patientId": patient_id,
            "specialty": specialty,
            "reason": reason
        })

    def order_test(self, patient_id: str, test_type: str) -> dict:
        """Order a test for a patient."""
        return self.post_action("gp", {
            "type": "order_test",
            "patientId": patient_id,
            "testType": test_type
        })

    def send_message(self, channel: str, message: str, patient_id: str = None) -> dict:
        """Send a message to a channel."""
        action = {
            "type": "send_message",
            "channel": channel,
            "message": message
        }
        if patient_id:
            action["patientId"] = patient_id
        return self.post_action("hospital", action)

    def book_appointment(self, patient_id: str, appointment_type: str) -> dict:
        """Book an appointment for a patient."""
        return self.post_action("gp", {
            "type": "book_appointment",
            "patientId": patient_id,
            "appointmentType": appointment_type
        })


# =============================================================================
# PATIENT DATA EXTRACTOR
# =============================================================================

class PatientDataExtractor:
    """Extract and format patient data from NHS-SIM for classification."""

    def __init__(self, client: NHSSimClient):
        self.client = client

    def get_all_patients(self) -> List[dict]:
        """Get all patients with their data from all sources."""
        patients = {}

        # Get GP data
        gp_data = self.client.get_site_view("gp")
        for resource in gp_data.get("resources", []):
            pid = resource.get("patientId")
            if not pid:
                continue

            if pid not in patients:
                patients[pid] = {
                    "id": pid,
                    "problems": [],
                    "blood_results": [],
                    "encounters": [],
                    "hospital_attendances": [],
                    "age": 65,  # Default - not available in sim
                    "sex": "U",
                    "smoking": False,
                    "systolic_bp": 130,
                    "diastolic_bp": 80,
                    "bmi": 27,
                }

            # Extract EHR data
            if resource.get("kind") == "ehr-record":
                data = resource.get("data", {})
                for problem in data.get("problems", []):
                    patients[pid]["problems"].append({
                        "term": problem.get("term", ""),
                        "date": problem.get("date", ""),
                        "status": problem.get("status", "active")
                    })

            # Extract encounters
            if resource.get("kind") == "encounter":
                data = resource.get("data", {})
                patients[pid]["encounters"].append({
                    "reason": data.get("reason", ""),
                    "channel": data.get("channel", "in-person")
                })

        # Get hospital data
        hospital_data = self.client.get_site_view("hospital")
        for resource in hospital_data.get("resources", []):
            pid = resource.get("patientId")
            if not pid or pid not in patients:
                continue

            # Extract blood results
            if resource.get("kind") == "report" and resource.get("data", {}).get("kind") == "blood-result":
                data = resource.get("data", {})
                analytes = []
                for a in data.get("analytes", []):
                    analytes.append({
                        "name": a.get("name", ""),
                        "value": a.get("value", 0),
                        "unit": a.get("unit", ""),
                        "ref_low": a.get("referenceLow", 0),
                        "ref_high": a.get("referenceHigh", 100),
                    })
                patients[pid]["blood_results"].append({
                    "panel_name": data.get("panel", {}).get("name", ""),
                    "analytes": analytes
                })

            # Extract hospital attendances
            if resource.get("kind") == "hospital-attendance":
                data = resource.get("data", {})
                patients[pid]["hospital_attendances"].append({
                    "presenting_complaint": data.get("presentingComplaint", ""),
                    "acuity": int(data.get("acuity", 3)),
                    "outcome": data.get("stage", "waiting")
                })

        return list(patients.values())

    def patient_to_text(self, patient: dict) -> str:
        """Convert patient to text timeline for classification."""
        lines = []

        # Demographics
        lines.append(f"Age {patient['age']} {patient['sex']} Smoker:{patient['smoking']} BP:{patient['systolic_bp']}/{patient['diastolic_bp']} BMI:{patient['bmi']}")

        # Problems
        for p in patient.get("problems", []):
            lines.append(f"{p.get('date', 'Unknown')}: {p['term']} ({p.get('status', 'active')})")

        # Blood results
        for br in patient.get("blood_results", []):
            abnormals = []
            for a in br.get("analytes", []):
                if a["value"] < a["ref_low"]:
                    abnormals.append(f"{a['name']} LOW")
                elif a["value"] > a["ref_high"]:
                    abnormals.append(f"{a['name']} HIGH")
            if abnormals:
                lines.append(f"Bloods: {', '.join(abnormals)}")

        # Hospital attendances
        for h in patient.get("hospital_attendances", []):
            lines.append(f"A&E: {h['presenting_complaint']} -> {h['outcome']}")

        return "\n".join(lines)


# =============================================================================
# STROKE PREVENTION AGENT
# =============================================================================

class StrokePreventionAgent:
    """Agent that monitors patients and takes preventive actions."""

    def __init__(self, client: NHSSimClient, model_path: str):
        self.client = client
        self.extractor = PatientDataExtractor(client)
        self.model = None
        self.model_path = model_path

        # Load model
        self._load_model()

    def _load_model(self):
        """Load the stroke risk classifier."""
        from stroke_classifier import StrokeRiskClassifier
        print(f"Loading stroke risk model from {self.model_path}...")
        self.model = StrokeRiskClassifier.load(self.model_path)

    def scan_population(self, risk_threshold: float = 0.3) -> List[dict]:
        """Scan all patients and identify high-risk individuals."""
        print("Fetching patient data from NHS-SIM...")
        patients = self.extractor.get_all_patients()
        print(f"Found {len(patients)} patients with records")

        high_risk = []

        for patient in patients:
            # Get prediction
            try:
                timeline_text = self.extractor.patient_to_text(patient)
                pred, prob = self.model.predict_text(timeline_text)

                patient["stroke_risk_predicted"] = prob
                patient["high_risk"] = pred

                if prob >= risk_threshold:
                    high_risk.append(patient)
                    print(f"  HIGH RISK: {patient['id']} - {prob*100:.1f}% risk")

            except Exception as e:
                print(f"  Error processing {patient['id']}: {e}")

        print(f"\nIdentified {len(high_risk)} high-risk patients (>{risk_threshold*100:.0f}% risk)")
        return high_risk

    def take_preventive_action(self, patient: dict) -> List[dict]:
        """Take preventive actions for a high-risk patient."""
        actions_taken = []
        pid = patient["id"]
        risk = patient.get("stroke_risk_predicted", 0)

        print(f"\nTaking action for {pid} (risk: {risk*100:.1f}%)...")

        # Check what conditions they have
        conditions = [p["term"] for p in patient.get("problems", [])]

        # 1. If AFib suspected but no diagnosis, order ECG
        has_afib = any("fibrillation" in c.lower() or "afib" in c.lower() for c in conditions)
        has_palpitations = any("palpitation" in h.get("presenting_complaint", "").lower()
                              for h in patient.get("hospital_attendances", []))

        if has_palpitations and not has_afib:
            try:
                result = self.client.order_test(pid, "ECG")
                actions_taken.append({"action": "order_test", "test": "ECG", "result": result})
                print(f"  Ordered ECG for suspected AFib")
            except Exception as e:
                print(f"  Failed to order ECG: {e}")

        # 2. If high risk, create cardiology referral
        if risk > 0.5:
            try:
                result = self.client.create_referral(
                    pid,
                    "cardiology",
                    f"Stroke prevention review - AI-predicted 5yr risk {risk*100:.0f}%"
                )
                actions_taken.append({"action": "create_referral", "specialty": "cardiology", "result": result})
                print(f"  Created cardiology referral")
            except Exception as e:
                print(f"  Failed to create referral: {e}")

        # 3. If diabetic with poor control, refer to diabetes clinic
        has_diabetes = any("diabetes" in c.lower() for c in conditions)
        has_high_hba1c = any(
            a["name"].lower() == "hba1c" and a["value"] > 53
            for br in patient.get("blood_results", [])
            for a in br.get("analytes", [])
        )

        if has_diabetes and has_high_hba1c:
            try:
                result = self.client.create_referral(
                    pid,
                    "diabetes",
                    "Poor glycaemic control - stroke risk reduction"
                )
                actions_taken.append({"action": "create_referral", "specialty": "diabetes", "result": result})
                print(f"  Created diabetes referral for poor HbA1c control")
            except Exception as e:
                print(f"  Failed to create diabetes referral: {e}")

        # 4. Alert care team
        try:
            result = self.client.send_message(
                "discharge-and-flow",
                f"STROKE PREVENTION ALERT: Patient {pid} flagged as high risk ({risk*100:.0f}%). Review recommended.",
                pid
            )
            actions_taken.append({"action": "send_message", "channel": "discharge-and-flow", "result": result})
            print(f"  Sent alert to care team")
        except Exception as e:
            print(f"  Failed to send alert: {e}")

        return actions_taken

    def run(self, risk_threshold: float = 0.3, take_actions: bool = True) -> dict:
        """Run the full prevention pipeline."""
        print("="*60)
        print("STROKE PREVENTION AGENT")
        print("="*60)

        # Get simulation status
        clock = self.client.get_clock()
        sim_time = datetime.fromtimestamp(clock["now"] / 1000)
        print(f"Simulation time: {sim_time}")
        print(f"Speed: {clock['speed']}x | Paused: {clock['paused']}")

        # Scan population
        high_risk = self.scan_population(risk_threshold)

        # Take actions
        results = {
            "timestamp": datetime.now().isoformat(),
            "sim_time": sim_time.isoformat(),
            "patients_scanned": len(self.extractor.get_all_patients()),
            "high_risk_identified": len(high_risk),
            "actions_taken": [],
            "high_risk_patients": []
        }

        for patient in high_risk:
            patient_summary = {
                "id": patient["id"],
                "risk": patient["stroke_risk_predicted"],
                "conditions": [p["term"] for p in patient.get("problems", [])
                              if p["term"] not in ["Medication review", "Preventive health review"]],
            }

            if take_actions:
                actions = self.take_preventive_action(patient)
                patient_summary["actions"] = actions
                results["actions_taken"].extend(actions)

            results["high_risk_patients"].append(patient_summary)

        # Summary
        print("\n" + "="*60)
        print("SUMMARY")
        print("="*60)
        print(f"Patients scanned: {results['patients_scanned']}")
        print(f"High-risk identified: {results['high_risk_identified']}")
        print(f"Actions taken: {len(results['actions_taken'])}")

        return results


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="NHS-SIM Stroke Prevention Agent")
    parser.add_argument("--token", required=True, help="NHS-SIM API token")
    parser.add_argument("--model", default="models/stroke_model.pkl", help="Path to trained model")
    parser.add_argument("--threshold", type=float, default=0.3, help="Risk threshold for intervention")
    parser.add_argument("--dry-run", action="store_true", help="Don't take actions, just identify")
    parser.add_argument("--output", help="Save results to JSON file")

    args = parser.parse_args()

    # Initialize
    client = NHSSimClient(args.token)
    agent = StrokePreventionAgent(client, args.model)

    # Run
    results = agent.run(
        risk_threshold=args.threshold,
        take_actions=not args.dry_run
    )

    # Save results
    if args.output:
        with open(args.output, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
