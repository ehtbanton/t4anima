#!/usr/bin/env python3
"""
Final NHS Patient Generator - Realistic Frequencies

UK NHS Statistics:
- Average: 3.3 GP visits/year
- Healthy patients: 1-2 visits/year
- Chronic conditions: 6-12 visits/year
- Blood tests: 0-1/year healthy, 2-4/year chronic
"""

import json
import random
import math
from datetime import datetime, timedelta
from typing import List
import argparse

PREVALENCE = {
    "Hypertension": {"18-39": 0.04, "40-54": 0.15, "55-64": 0.30, "65-74": 0.45, "75+": 0.55},
    "Type 2 Diabetes": {"18-39": 0.02, "40-54": 0.06, "55-64": 0.12, "65-74": 0.18, "75+": 0.19},
    "Atrial fibrillation": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.02, "65-74": 0.05, "75+": 0.12},
    "Heart failure": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.015, "65-74": 0.04, "75+": 0.10},
    "CKD Stage 3+": {"18-39": 0.01, "40-54": 0.03, "55-64": 0.06, "65-74": 0.12, "75+": 0.25},
    "Previous stroke": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.015, "65-74": 0.03, "75+": 0.08},
    "Previous TIA": {"18-39": 0.0005, "40-54": 0.002, "55-64": 0.008, "65-74": 0.015, "75+": 0.04},
    "COPD": {"18-39": 0.001, "40-54": 0.01, "55-64": 0.03, "65-74": 0.06, "75+": 0.07},
    "Asthma": {"18-39": 0.10, "40-54": 0.08, "55-64": 0.07, "65-74": 0.06, "75+": 0.05},
    "Hyperlipidaemia": {"18-39": 0.02, "40-54": 0.10, "55-64": 0.20, "65-74": 0.25, "75+": 0.22},
    "Obesity": {"18-39": 0.18, "40-54": 0.28, "55-64": 0.30, "65-74": 0.28, "75+": 0.20},
    "Depression": {"18-39": 0.08, "40-54": 0.10, "55-64": 0.08, "65-74": 0.06, "75+": 0.05},
    "Hypothyroidism": {"18-39": 0.02, "40-54": 0.03, "55-64": 0.05, "65-74": 0.07, "75+": 0.08},
    "Osteoarthritis": {"18-39": 0.01, "40-54": 0.05, "55-64": 0.12, "65-74": 0.20, "75+": 0.32},
}

SMOKING = {"18-39": 0.16, "40-54": 0.14, "55-64": 0.12, "65-74": 0.08, "75+": 0.04}

STROKE_RISK_CONDITIONS = {"Atrial fibrillation", "Hypertension", "Type 2 Diabetes",
                          "Heart failure", "CKD Stage 3+", "Previous stroke", "Previous TIA"}

REPORT_TYPES = [
    {"type": "FBC", "name": "Full Blood Count", "analytes": ["Hb", "WCC", "Platelets", "MCV"]},
    {"type": "U&E", "name": "Urea & Electrolytes", "analytes": ["Na", "K", "Creatinine", "eGFR"]},
    {"type": "LFT", "name": "Liver Function Tests", "analytes": ["ALT", "ALP", "Bilirubin", "Albumin"]},
    {"type": "HbA1c", "name": "HbA1c", "analytes": ["HbA1c"]},
    {"type": "Lipids", "name": "Lipid Profile", "analytes": ["Total Chol", "HDL", "LDL", "Triglycerides"]},
    {"type": "TFT", "name": "Thyroid Function", "analytes": ["TSH", "Free T4"]},
    {"type": "BNP", "name": "BNP/NT-proBNP", "analytes": ["BNP"]},
    {"type": "INR", "name": "INR", "analytes": ["INR"]},
    {"type": "ECG", "name": "ECG", "analytes": []},
    {"type": "Echo", "name": "Echocardiogram", "analytes": ["EF", "LV function"]},
    {"type": "CXR", "name": "Chest X-Ray", "analytes": []},
    {"type": "CT-Head", "name": "CT Head", "analytes": []},
    {"type": "Carotid-USS", "name": "Carotid Doppler", "analytes": ["Stenosis %"]},
    {"type": "24h-BP", "name": "24hr BP Monitor", "analytes": ["Mean SBP", "Mean DBP"]},
    {"type": "Holter", "name": "Holter Monitor", "analytes": ["Rhythm"]},
]

def get_age_band(age):
    if age < 40: return "18-39"
    elif age < 55: return "40-54"
    elif age < 65: return "55-64"
    elif age < 75: return "65-74"
    else: return "75+"

def calculate_stroke_risk(age, sex, conditions, sbp, smoking):
    base = 0.0008 * math.exp(0.07 * (age - 40))
    base = min(base, 0.12)

    m = 1.0
    conds = set(c.lower() for c in conditions)

    if any("atrial" in c or "fibrillation" in c for c in conds): m *= 5.0
    if any("stroke" in c for c in conds): m *= 3.0
    if any("tia" in c for c in conds): m *= 2.5
    if any("hypertension" in c for c in conds): m *= 2.0
    if any("diabetes" in c for c in conds): m *= 1.5
    if any("heart failure" in c for c in conds): m *= 1.4
    if any("ckd" in c for c in conds): m *= 1.3
    if smoking: m *= 1.4
    if sex == "M": m *= 1.15
    if sbp > 140: m *= 1 + (sbp - 140) * 0.008

    return min(base * m, 0.90)


class PatientGenerator:
    def __init__(self, seed=42):
        random.seed(seed)
        self.now = datetime(2026, 9, 12)

    def generate(self, pid: str) -> dict:
        age = self._age()
        sex = random.choice(["M", "F"])
        band = get_age_band(age)
        smoking = random.random() < SMOKING.get(band, 0.1)

        conditions = [c for c in PREVALENCE if random.random() < PREVALENCE[c].get(band, 0)]
        conditions = self._add_comorbidities(conditions, age)

        has_chronic = any(c in STROKE_RISK_CONDITIONS for c in conditions)
        sbp = self._bp(conditions, age)

        patient = {
            "id": pid,
            "age": age,
            "sex": sex,
            "smoking": smoking,
            "sbp": round(sbp),
            "dbp": round(sbp * random.uniform(0.55, 0.62)),
            "conditions": [],
            "events": [],
        }

        # Generate timeline
        self._generate_timeline(patient, conditions, has_chronic, age)

        # Calculate final risk
        cond_names = [c["term"] for c in patient["conditions"]]
        patient["stroke_risk"] = round(calculate_stroke_risk(age, sex, cond_names, sbp, smoking), 4)
        patient["will_stroke"] = random.random() < patient["stroke_risk"]

        return patient

    def _age(self):
        weights = [(20, 39, 0.10), (40, 54, 0.18), (55, 64, 0.24), (65, 74, 0.30), (75, 90, 0.18)]
        r = random.random()
        c = 0
        for lo, hi, w in weights:
            c += w
            if r < c: return random.randint(lo, hi)
        return 68

    def _add_comorbidities(self, conds, age):
        new = conds.copy()
        if "Type 2 Diabetes" in conds and random.random() < 0.4:
            if "Hypertension" not in new: new.append("Hypertension")
        if "Hypertension" in conds and age > 60:
            if random.random() < 0.12 and "Atrial fibrillation" not in new:
                new.append("Atrial fibrillation")
        if "Atrial fibrillation" in conds and random.random() < 0.06:
            if "Previous stroke" not in new: new.append("Previous stroke")
        return new

    def _bp(self, conds, age):
        base = 115 + age * 0.25
        if "Hypertension" in conds:
            base = random.gauss(145 if random.random() < 0.4 else 135, 12)
        return max(95, min(195, random.gauss(base, 8)))

    def _generate_timeline(self, patient, conditions, has_chronic, age):
        events = []

        # Add condition diagnoses over past 10 years
        for cond in conditions:
            years_ago = random.uniform(0.5, min(12, age - 20))
            date = self.now - timedelta(days=int(years_ago * 365))
            patient["conditions"].append({
                "term": cond,
                "date": date.strftime("%Y-%m-%d"),
                "timestamp": int(date.timestamp() * 1000)
            })
            events.append({
                "type": "diagnosis",
                "date": date.strftime("%Y-%m-%d"),
                "timestamp": int(date.timestamp() * 1000),
                "title": f"Diagnosed: {cond}",
                "detail": cond
            })

        # Generate events for past 5 years
        for year in range(5):
            year_start = self.now - timedelta(days=(5 - year) * 365)

            # GP visits: 1-3 for healthy, 4-10 for chronic
            n_visits = random.randint(4, 10) if has_chronic else random.randint(1, 3)
            for _ in range(n_visits):
                date = year_start + timedelta(days=random.randint(0, 364))
                if date < self.now:
                    events.append(self._gp_visit(date, conditions))

            # Reports: 0-1 for healthy, 2-4 for chronic
            n_reports = random.randint(2, 4) if has_chronic else random.randint(0, 1)
            for _ in range(n_reports):
                date = year_start + timedelta(days=random.randint(0, 364))
                if date < self.now:
                    events.append(self._report(date, conditions))

        # Hospital events (rare)
        if random.random() < (0.3 if has_chronic else 0.05):
            for _ in range(random.randint(1, 2)):
                date = self.now - timedelta(days=random.randint(30, 1500))
                events.append(self._hospital(date, conditions))

        patient["events"] = sorted(events, key=lambda e: e["timestamp"])

    def _gp_visit(self, date, conditions):
        reasons = ["Routine review", "Medication review", "BP check", "Blood test review",
                   "Sick note", "Prescription request", "Telephone consultation", "Annual review"]
        if conditions:
            reasons.extend([f"{c} review" for c in conditions[:2]])

        return {
            "type": "gp",
            "date": date.strftime("%Y-%m-%d"),
            "timestamp": int(date.timestamp() * 1000),
            "title": random.choice(reasons),
            "detail": random.choice(["in-person", "telephone", "video"])
        }

    def _report(self, date, conditions):
        # Weight report types by conditions
        weights = {r["type"]: 1 for r in REPORT_TYPES}
        if "Type 2 Diabetes" in conditions: weights["HbA1c"] = 5
        if "Hypothyroidism" in conditions: weights["TFT"] = 4
        if "Heart failure" in conditions: weights["BNP"] = 4; weights["Echo"] = 3
        if "Atrial fibrillation" in conditions: weights["ECG"] = 4; weights["INR"] = 3
        if "CKD Stage 3+" in conditions: weights["U&E"] = 4
        if "Previous stroke" in conditions: weights["CT-Head"] = 2; weights["Carotid-USS"] = 3

        report = random.choices(REPORT_TYPES, weights=[weights.get(r["type"], 1) for r in REPORT_TYPES])[0]

        # Generate result
        abnormal = random.random() < 0.25
        if abnormal and report["analytes"]:
            result = f"{random.choice(report['analytes'])} abnormal"
        else:
            result = "Normal" if report["analytes"] else "NAD"

        return {
            "type": "report",
            "date": date.strftime("%Y-%m-%d"),
            "timestamp": int(date.timestamp() * 1000),
            "title": report["name"],
            "detail": result,
            "report_type": report["type"]
        }

    def _hospital(self, date, conditions):
        complaints = ["Chest pain", "Breathlessness", "Collapse", "Palpitations", "Dizzy spell"]
        if "Heart failure" in conditions: complaints.extend(["SOB", "Leg swelling"])
        if "COPD" in conditions: complaints.extend(["Exacerbation", "Chest infection"])

        return {
            "type": "hospital",
            "date": date.strftime("%Y-%m-%d"),
            "timestamp": int(date.timestamp() * 1000),
            "title": f"A&E: {random.choice(complaints)}",
            "detail": random.choice(["Discharged", "Admitted 2 days", "Admitted 5 days"])
        }

    def generate_cohort(self, n):
        patients = []
        for i in range(n):
            patients.append(self.generate(f"P{i:05d}"))
            if (i+1) % 1000 == 0: print(f"  {i+1}/{n}")
        return patients


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=5000)
    parser.add_argument("-o", default="data/patients_final.json")
    args = parser.parse_args()

    print(f"Generating {args.n} patients...")
    gen = PatientGenerator()
    patients = gen.generate_cohort(args.n)

    # Stats
    strokes = sum(p["will_stroke"] for p in patients)
    avg_events = sum(len(p["events"]) for p in patients) / len(patients)
    afib = sum(1 for p in patients if any("Atrial" in c["term"] for c in p["conditions"]))

    print(f"\nStrokes: {strokes} ({100*strokes/len(patients):.1f}%)")
    print(f"AFib patients: {afib} ({100*afib/len(patients):.1f}%)")
    print(f"Avg events/patient: {avg_events:.1f}")

    with open(args.o, "w") as f:
        json.dump(patients, f)
    print(f"Saved: {args.o}")


if __name__ == "__main__":
    main()
