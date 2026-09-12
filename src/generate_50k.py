#!/usr/bin/env python3
"""
Generate 50k patients with time-dependent risk and full report text.
Risk evolves based on:
- Age progression
- Abnormal test results (even without diagnosis)
- Accumulating risk signals in reports
- BP trends
- Hospital attendances
"""

import json
import random
import math
from datetime import datetime, timedelta
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_with_reports import ReportGenerator, PREVALENCE, SMOKING, get_age_band

def calculate_stroke_risk_at_time(patient_base, events_so_far, current_date):
    """Calculate risk based on all information available at a point in time."""

    age_at_time = patient_base['base_age'] + (current_date - patient_base['start_date']).days / 365

    # Base risk increases exponentially with age
    base = 0.0005 * math.exp(0.065 * (age_at_time - 40))
    base = min(base, 0.10)

    multiplier = 1.0

    # Static factors
    if patient_base['smoking']:
        multiplier *= 1.3
    if patient_base['sex'] == 'M':
        multiplier *= 1.12

    # Analyze events for risk signals
    diagnosed_conditions = set()
    abnormal_count = 0
    high_bp_readings = 0
    af_signals = 0
    hospital_visits = 0
    recent_hospital = False

    six_months_ago = current_date - timedelta(days=180)

    for e in events_so_far:
        event_date = datetime.strptime(e['date'], '%Y-%m-%d')

        if e['type'] == 'diagnosis':
            diagnosed_conditions.add(e['detail'].lower())

        elif e['type'] == 'report':
            report_text = e.get('report_text', '').lower()

            # Count abnormals - they matter even without diagnosis
            if 'abnormal' in e.get('detail', '').lower():
                abnormal_count += 1

            # Look for specific risk signals in report text
            if 'atrial fibrillation' in report_text or 'af' in report_text and 'irregularly irregular' in report_text:
                af_signals += 1
            if 'hypertension' in report_text or 'elevated' in report_text and 'bp' in report_text:
                high_bp_readings += 1
            if 'stenosis' in report_text and '%' in report_text:
                # Carotid stenosis found
                multiplier *= 1.15
            if 'impaired' in report_text and ('lv' in report_text or 'ejection' in report_text):
                multiplier *= 1.2
            if 'infarct' in report_text or 'ischaemi' in report_text:
                multiplier *= 1.25

        elif e['type'] == 'hospital':
            hospital_visits += 1
            if event_date > six_months_ago:
                recent_hospital = True
            # Hospital presentations are risk signals
            if 'chest pain' in e['title'].lower() or 'palpitation' in e['title'].lower():
                multiplier *= 1.1
            if 'collapse' in e['title'].lower() or 'dizzy' in e['title'].lower():
                multiplier *= 1.08

        elif e['type'] == 'gp':
            # Frequent GP visits might indicate concern
            pass

    # Apply diagnosed condition multipliers
    if any('atrial' in c or 'fibrillation' in c for c in diagnosed_conditions):
        multiplier *= 4.5
    elif af_signals >= 2:  # AF detected but maybe not formally diagnosed yet
        multiplier *= 2.5

    if any('stroke' in c for c in diagnosed_conditions):
        multiplier *= 3.0
    if any('tia' in c for c in diagnosed_conditions):
        multiplier *= 2.2
    if any('hypertension' in c for c in diagnosed_conditions):
        multiplier *= 1.8
    elif high_bp_readings >= 3:  # Elevated BP seen but not diagnosed
        multiplier *= 1.3

    if any('diabetes' in c for c in diagnosed_conditions):
        multiplier *= 1.5
    if any('heart failure' in c for c in diagnosed_conditions):
        multiplier *= 1.4
    if any('ckd' in c for c in diagnosed_conditions):
        multiplier *= 1.25

    # Abnormal results accumulate risk
    if abnormal_count >= 5:
        multiplier *= 1.2
    elif abnormal_count >= 3:
        multiplier *= 1.1

    # Recent hospital visit is a risk signal
    if recent_hospital:
        multiplier *= 1.15

    # BP from patient record
    if patient_base['sbp'] > 160:
        multiplier *= 1.3
    elif patient_base['sbp'] > 140:
        multiplier *= 1.15

    risk = base * multiplier

    # Add small random walk component for temporal variation
    noise = random.gauss(0, 0.005)
    risk = max(0.001, min(0.85, risk + noise))

    return round(risk, 4)


class PatientGenerator:
    def __init__(self, seed=42):
        random.seed(seed)
        self.now = datetime(2026, 9, 12)
        self.reports = ReportGenerator()

    def _age(self):
        weights = [(20, 39, 0.08), (40, 54, 0.18), (55, 64, 0.26), (65, 74, 0.32), (75, 90, 0.16)]
        r = random.random()
        c = 0
        for lo, hi, w in weights:
            c += w
            if r < c: return random.randint(lo, hi)
        return 68

    def _add_comorbidities(self, conds, age):
        new = conds.copy()
        if "Type 2 Diabetes" in conds and random.random() < 0.45:
            if "Hypertension" not in new: new.append("Hypertension")
        if "Hypertension" in conds and age > 55:
            if random.random() < 0.15 and "Atrial fibrillation" not in new:
                new.append("Atrial fibrillation")
        if "Atrial fibrillation" in conds and random.random() < 0.08:
            if "Previous stroke" not in new: new.append("Previous stroke")
        if "Hypertension" in conds and age > 65 and random.random() < 0.1:
            if "Heart failure" not in new: new.append("Heart failure")
        return new

    def _bp(self, conds, age):
        base = 112 + age * 0.3
        if "Hypertension" in conds:
            base = random.gauss(148 if random.random() < 0.35 else 138, 14)
        return max(92, min(198, random.gauss(base, 10)))

    def generate(self, pid):
        age = self._age()
        sex = random.choice(["M", "F"])
        band = get_age_band(age)
        smoking = random.random() < SMOKING.get(band, 0.1)

        conditions = [c for c in PREVALENCE if random.random() < PREVALENCE[c].get(band, 0)]
        conditions = self._add_comorbidities(conditions, age)

        has_chronic = len(conditions) > 0
        sbp = self._bp(conditions, age)

        start_date = self.now - timedelta(days=5*365)

        patient_base = {
            "id": pid,
            "base_age": age - 5,  # Age at start of timeline
            "age": age,
            "sex": sex,
            "smoking": smoking,
            "sbp": round(sbp),
            "dbp": round(sbp * random.uniform(0.54, 0.63)),
            "start_date": start_date,
            "conditions": [],
            "events": [],
        }

        self._generate_timeline(patient_base, conditions, has_chronic)

        # Calculate final risk using all events
        patient_base["stroke_risk"] = calculate_stroke_risk_at_time(
            patient_base, patient_base["events"], self.now
        )

        # Determine stroke outcome - weighted by final risk
        patient_base["will_stroke"] = random.random() < patient_base["stroke_risk"]

        # Store risk trajectory for time machine
        patient_base["risk_trajectory"] = self._calculate_trajectory(patient_base)

        # Clean up internal fields
        del patient_base["start_date"]
        del patient_base["base_age"]

        return patient_base

    def _calculate_trajectory(self, patient):
        """Calculate risk at each event point for the time machine."""
        trajectory = []
        for i in range(len(patient["events"]) + 1):
            events_so_far = patient["events"][:i]
            if i == 0:
                date = patient["start_date"]
            else:
                date = datetime.strptime(patient["events"][i-1]["date"], "%Y-%m-%d")

            risk = calculate_stroke_risk_at_time(patient, events_so_far, date)
            trajectory.append(risk)
        return trajectory

    def _generate_timeline(self, patient, conditions, has_chronic):
        events = []
        start_date = patient["start_date"]

        # Add condition diagnoses spread over the timeline
        for cond in conditions:
            years_ago = random.uniform(0.5, 4.5)
            date = self.now - timedelta(days=int(years_ago * 365))
            if date < start_date:
                date = start_date + timedelta(days=random.randint(30, 365))

            patient["conditions"].append({
                "term": cond,
                "date": date.strftime("%Y-%m-%d"),
            })
            event = {
                "type": "diagnosis",
                "date": date.strftime("%Y-%m-%d"),
                "timestamp": int(date.timestamp() * 1000),
                "title": f"Diagnosed: {cond}",
                "detail": cond
            }
            event["report_text"] = self.reports.generate_report(event, patient, conditions)
            events.append(event)

        report_types = ["FBC", "U&E", "Lipids", "HbA1c", "ECG", "Echo", "CT-Head", "Carotid-USS"]

        # Weight report types by conditions
        weights = {r: 1 for r in report_types}
        if "Type 2 Diabetes" in conditions: weights["HbA1c"] = 4
        if "Heart failure" in conditions: weights["Echo"] = 3; weights["ECG"] = 2
        if "Atrial fibrillation" in conditions: weights["ECG"] = 4
        if "CKD Stage 3+" in conditions: weights["U&E"] = 3
        if "Previous stroke" in conditions: weights["CT-Head"] = 2; weights["Carotid-USS"] = 3

        for year in range(5):
            year_start = start_date + timedelta(days=year * 365)

            # GP visits
            n_visits = random.randint(5, 12) if has_chronic else random.randint(1, 4)
            for _ in range(n_visits):
                date = year_start + timedelta(days=random.randint(0, 364))
                if date < self.now:
                    reasons = ["Routine review", "Medication review", "BP check", "Annual review", "Blood test review"]
                    if conditions:
                        reasons.extend([f"{c} review" for c in conditions[:2]])
                    event = {
                        "type": "gp",
                        "date": date.strftime("%Y-%m-%d"),
                        "timestamp": int(date.timestamp() * 1000),
                        "title": random.choice(reasons),
                        "detail": random.choice(["in-person", "telephone", "video"])
                    }
                    event["report_text"] = self.reports.generate_report(event, patient, conditions)
                    events.append(event)

            # Reports - abnormal rate increases if patient has risk factors
            base_abnormal_rate = 0.15 if not has_chronic else 0.30
            n_reports = random.randint(2, 5) if has_chronic else random.randint(0, 2)

            for _ in range(n_reports):
                date = year_start + timedelta(days=random.randint(0, 364))
                if date < self.now:
                    rtype = random.choices(
                        report_types,
                        weights=[weights.get(r, 1) for r in report_types]
                    )[0]

                    # Abnormal rate increases later in timeline for at-risk patients
                    abnormal_boost = 0.1 * year if has_chronic else 0
                    abnormal = random.random() < (base_abnormal_rate + abnormal_boost)

                    event = {
                        "type": "report",
                        "date": date.strftime("%Y-%m-%d"),
                        "timestamp": int(date.timestamp() * 1000),
                        "title": rtype,
                        "detail": "abnormal" if abnormal else "normal",
                        "report_type": rtype
                    }
                    event["report_text"] = self.reports.generate_report(event, patient, conditions)
                    events.append(event)

        # Hospital events
        if random.random() < (0.35 if has_chronic else 0.06):
            n_hosp = random.randint(1, 3) if has_chronic else 1
            for _ in range(n_hosp):
                date = start_date + timedelta(days=random.randint(180, 1800))
                if date < self.now:
                    complaints = ["Chest pain", "Breathlessness", "Collapse", "Palpitations", "Dizzy spell"]
                    if "Heart failure" in conditions: complaints.extend(["SOB", "Leg swelling"])
                    event = {
                        "type": "hospital",
                        "date": date.strftime("%Y-%m-%d"),
                        "timestamp": int(date.timestamp() * 1000),
                        "title": f"A&E: {random.choice(complaints)}",
                        "detail": random.choice(["Discharged", "Admitted 2 days", "Admitted 5 days"])
                    }
                    event["report_text"] = self.reports.generate_report(event, patient, conditions)
                    events.append(event)

        patient["events"] = sorted(events, key=lambda e: e["timestamp"])

    def generate_cohort(self, n):
        patients = []
        for i in range(n):
            patients.append(self.generate(f"P{i:05d}"))
            if (i+1) % 1000 == 0:
                print(f"  {i+1}/{n}")
        return patients


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=50000)
    parser.add_argument("-o", default="data/patients_50k.json")
    args = parser.parse_args()

    print(f"Generating {args.n} patients with time-dependent risk...")
    gen = PatientGenerator()
    patients = gen.generate_cohort(args.n)

    strokes = sum(p["will_stroke"] for p in patients)
    avg_events = sum(len(p["events"]) for p in patients) / len(patients)
    avg_risk = sum(p["stroke_risk"] for p in patients) / len(patients)

    print(f"\nStrokes: {strokes} ({100*strokes/len(patients):.1f}%)")
    print(f"Avg events/patient: {avg_events:.1f}")
    print(f"Avg final risk: {100*avg_risk:.2f}%")

    with open(args.o, "w") as f:
        json.dump(patients, f)
    print(f"Saved: {args.o}")
