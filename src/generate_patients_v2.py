#!/usr/bin/env python3
"""
Synthetic NHS Patient Generator V2 - More Realistic Distributions

Based on UK NHS statistics:
- Average GP visits: 3.3/year for healthy, 13-21/year for chronic conditions
- Blood tests: 1-2/year healthy, 4-8/year for monitored conditions
- Hospital attendance: varies by condition and age
"""

import json
import random
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import argparse

# =============================================================================
# REALISTIC UK PREVALENCE RATES
# =============================================================================

PREVALENCE = {
    "Hypertension": {"18-39": 0.04, "40-54": 0.15, "55-64": 0.30, "65-74": 0.45, "75-84": 0.55, "85+": 0.60},
    "Type 2 Diabetes": {"18-39": 0.02, "40-54": 0.06, "55-64": 0.12, "65-74": 0.18, "75-84": 0.20, "85+": 0.18},
    "Atrial fibrillation": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.02, "65-74": 0.05, "75-84": 0.10, "85+": 0.15},
    "Heart failure": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.015, "65-74": 0.04, "75-84": 0.08, "85+": 0.12},
    "Chronic kidney disease": {"18-39": 0.01, "40-54": 0.03, "55-64": 0.06, "65-74": 0.12, "75-84": 0.20, "85+": 0.30},
    "Previous stroke": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.015, "65-74": 0.03, "75-84": 0.06, "85+": 0.10},
    "Previous TIA": {"18-39": 0.0005, "40-54": 0.002, "55-64": 0.008, "65-74": 0.015, "75-84": 0.03, "85+": 0.05},
    "COPD": {"18-39": 0.001, "40-54": 0.01, "55-64": 0.03, "65-74": 0.06, "75-84": 0.08, "85+": 0.08},
    "Asthma": {"18-39": 0.10, "40-54": 0.08, "55-64": 0.07, "65-74": 0.06, "75-84": 0.05, "85+": 0.04},
    "Depression": {"18-39": 0.08, "40-54": 0.10, "55-64": 0.08, "65-74": 0.06, "75-84": 0.05, "85+": 0.05},
    "Anxiety": {"18-39": 0.06, "40-54": 0.07, "55-64": 0.05, "65-74": 0.04, "75-84": 0.03, "85+": 0.03},
    "Osteoarthritis": {"18-39": 0.01, "40-54": 0.05, "55-64": 0.12, "65-74": 0.20, "75-84": 0.30, "85+": 0.35},
    "Hypothyroidism": {"18-39": 0.02, "40-54": 0.03, "55-64": 0.05, "65-74": 0.07, "75-84": 0.08, "85+": 0.09},
}

SMOKING_RATES = {"18-39": 0.18, "40-54": 0.16, "55-64": 0.14, "65-74": 0.10, "75-84": 0.06, "85+": 0.03}

# Conditions that require regular monitoring
MONITORED_CONDITIONS = {"Type 2 Diabetes", "Chronic kidney disease", "Heart failure",
                        "Atrial fibrillation", "Hypertension", "Hypothyroidism"}

BLOOD_PANELS = {
    "FBC": {
        "Haemoglobin": {"unit": "g/L", "ref_low": 115, "ref_high": 165, "mean": 140, "std": 12},
        "White cell count": {"unit": "×10⁹/L", "ref_low": 4, "ref_high": 11, "mean": 7, "std": 2},
        "Platelets": {"unit": "×10⁹/L", "ref_low": 150, "ref_high": 400, "mean": 250, "std": 50},
    },
    "U&E": {
        "Sodium": {"unit": "mmol/L", "ref_low": 133, "ref_high": 146, "mean": 140, "std": 3},
        "Potassium": {"unit": "mmol/L", "ref_low": 3.5, "ref_high": 5.3, "mean": 4.2, "std": 0.4},
        "Creatinine": {"unit": "µmol/L", "ref_low": 45, "ref_high": 110, "mean": 80, "std": 20},
        "eGFR": {"unit": "mL/min", "ref_low": 60, "ref_high": 120, "mean": 90, "std": 20},
    },
    "HbA1c": {
        "HbA1c": {"unit": "mmol/mol", "ref_low": 20, "ref_high": 41, "mean": 35, "std": 8},
    },
    "Lipids": {
        "Total cholesterol": {"unit": "mmol/L", "ref_low": 0, "ref_high": 5, "mean": 5.2, "std": 1.0},
        "HDL": {"unit": "mmol/L", "ref_low": 1, "ref_high": 2.5, "mean": 1.4, "std": 0.4},
        "LDL": {"unit": "mmol/L", "ref_low": 0, "ref_high": 3, "mean": 3.0, "std": 0.8},
    },
    "LFT": {
        "ALT": {"unit": "U/L", "ref_low": 0, "ref_high": 40, "mean": 25, "std": 12},
        "Bilirubin": {"unit": "µmol/L", "ref_low": 0, "ref_high": 21, "mean": 12, "std": 5},
    },
    "TFT": {
        "TSH": {"unit": "mU/L", "ref_low": 0.4, "ref_high": 4.0, "mean": 2.0, "std": 1.0},
    },
    "BNP": {
        "BNP": {"unit": "pg/mL", "ref_low": 0, "ref_high": 100, "mean": 50, "std": 80},
    },
}

def get_age_band(age: int) -> str:
    if age < 40: return "18-39"
    elif age < 55: return "40-54"
    elif age < 65: return "55-64"
    elif age < 75: return "65-74"
    elif age < 85: return "75-84"
    else: return "85+"

def has_condition(age: int, condition: str) -> bool:
    age_band = get_age_band(age)
    prevalence = PREVALENCE.get(condition, {}).get(age_band, 0)
    return random.random() < prevalence

def generate_blood_value(config: dict, modifier: float = 1.0) -> float:
    mean = config["mean"] * modifier
    value = random.gauss(mean, config["std"])
    min_val = config["ref_low"] * 0.3
    max_val = config["ref_high"] * 2.5
    return round(max(min_val, min(max_val, value)), 1)

def calculate_stroke_risk(age, sex, conditions, systolic_bp, smoking, hba1c=35, hdl=1.4, chol=5):
    base_risk = 0.001 * math.exp(0.08 * (age - 40))
    base_risk = min(base_risk, 0.15)

    multiplier = 1.0
    cond_lower = [c.lower() for c in conditions]

    if any("atrial fibrillation" in c for c in cond_lower): multiplier *= 5.0
    if any("stroke" in c or "tia" in c for c in cond_lower): multiplier *= 3.0
    if any("hypertension" in c for c in cond_lower): multiplier *= 2.0
    if any("diabetes" in c for c in cond_lower): multiplier *= 1.5
    if any("heart failure" in c for c in cond_lower): multiplier *= 1.5
    if any("kidney" in c for c in cond_lower): multiplier *= 1.3
    if smoking: multiplier *= 1.5
    if sex == "M": multiplier *= 1.2
    if systolic_bp > 140: multiplier *= 1.0 + (systolic_bp - 140) * 0.01
    if hba1c > 53: multiplier *= 1.1
    if hdl > 0 and chol / hdl > 4: multiplier *= 1.1

    return min(base_risk * multiplier, 0.95)


class PatientGenerator:
    def __init__(self, seed=42):
        random.seed(seed)
        self.base_date = datetime(2026, 9, 12)

    def generate_patient(self, patient_id: str) -> dict:
        # Demographics - weighted toward older (healthcare utilizers)
        age = self._weighted_age()
        sex = random.choice(["M", "F"])
        age_band = get_age_band(age)
        smoking = random.random() < SMOKING_RATES.get(age_band, 0.1)

        # Generate conditions
        conditions = []
        for condition in PREVALENCE.keys():
            if has_condition(age, condition):
                conditions.append(condition)

        # Add comorbidities (they cluster)
        conditions = self._add_comorbidities(conditions, age)

        # Determine patient "type" for visit frequency
        has_chronic = any(c in MONITORED_CONDITIONS for c in conditions)
        is_frequent_attender = has_chronic or random.random() < 0.1  # 10% frequent attenders

        # Generate vitals
        systolic_bp = self._generate_bp(conditions, age)
        diastolic_bp = systolic_bp * random.uniform(0.55, 0.65)
        bmi = self._generate_bmi(conditions)

        patient = {
            "id": patient_id,
            "age": age,
            "sex": sex,
            "smoking": smoking,
            "systolic_bp": round(systolic_bp),
            "diastolic_bp": round(diastolic_bp),
            "bmi": round(bmi, 1),
            "is_frequent_attender": is_frequent_attender,
            "problems": [],
            "blood_results": [],
            "encounters": [],
            "hospital_attendances": [],
        }

        # Generate problems with onset dates
        patient["problems"] = self._generate_problems(conditions, age)

        # Generate realistic encounter/blood test frequency
        patient["encounters"], patient["blood_results"] = self._generate_clinical_events(
            conditions, age, is_frequent_attender
        )

        # Hospital attendances
        if random.random() < self._hospital_probability(age, conditions):
            patient["hospital_attendances"] = self._generate_hospital_attendances(conditions)

        # Calculate stroke risk
        condition_names = [p["term"] for p in patient["problems"]]
        hba1c = self._get_latest_value(patient["blood_results"], "HbA1c", 35)
        hdl = self._get_latest_value(patient["blood_results"], "HDL", 1.4)
        chol = self._get_latest_value(patient["blood_results"], "Total cholesterol", 5)

        patient["stroke_risk_5yr"] = calculate_stroke_risk(
            age, sex, condition_names, systolic_bp, smoking, hba1c, hdl, chol
        )
        patient["will_have_stroke"] = random.random() < patient["stroke_risk_5yr"]

        return patient

    def _weighted_age(self) -> int:
        weights = [(18, 39, 0.12), (40, 54, 0.18), (55, 64, 0.22),
                   (65, 74, 0.28), (75, 84, 0.15), (85, 95, 0.05)]
        r = random.random()
        cumulative = 0
        for low, high, weight in weights:
            cumulative += weight
            if r < cumulative:
                return random.randint(low, high)
        return 70

    def _add_comorbidities(self, conditions: List[str], age: int) -> List[str]:
        new_conditions = conditions.copy()

        if "Type 2 Diabetes" in conditions:
            if random.random() < 0.5 and "Hypertension" not in new_conditions:
                new_conditions.append("Hypertension")

        if "Hypertension" in conditions and age > 60:
            if random.random() < 0.15 and "Heart failure" not in new_conditions:
                new_conditions.append("Heart failure")
            if random.random() < 0.1 and "Atrial fibrillation" not in new_conditions:
                new_conditions.append("Atrial fibrillation")

        if "Atrial fibrillation" in conditions:
            if random.random() < 0.08 and "Previous stroke" not in new_conditions:
                new_conditions.append("Previous stroke")

        return new_conditions

    def _generate_bp(self, conditions: List[str], age: int) -> float:
        base = 110 + age * 0.3
        if "Hypertension" in conditions:
            if random.random() < 0.6:
                base = random.gauss(135, 10)
            else:
                base = random.gauss(155, 15)
        else:
            base = random.gauss(base, 10)
        return max(90, min(200, base))

    def _generate_bmi(self, conditions: List[str]) -> float:
        if "Type 2 Diabetes" in conditions:
            return random.gauss(30, 5)
        return random.gauss(26, 4)

    def _generate_problems(self, conditions: List[str], age: int) -> List[dict]:
        problems = []
        for i, condition in enumerate(conditions):
            years_ago = random.randint(1, max(1, min(15, age - 20)))
            onset_date = self.base_date - timedelta(days=years_ago * 365 + random.randint(0, 180))

            problems.append({
                "code": f"COND-{i+1:04d}",
                "term": condition,
                "date": onset_date.strftime("%Y-%m-%d"),
                "status": "active"
            })

        return sorted(problems, key=lambda p: p["date"])

    def _generate_clinical_events(self, conditions: List[str], age: int, is_frequent: bool):
        """Generate realistic encounters and blood tests over 5 years."""
        encounters = []
        blood_results = []

        has_monitored = any(c in MONITORED_CONDITIONS for c in conditions)

        # Determine yearly frequencies based on NHS data
        if is_frequent:
            encounters_per_year = random.randint(8, 20)  # Frequent attenders: 13-21 median
            bloods_per_year = random.randint(3, 6) if has_monitored else random.randint(1, 2)
        else:
            encounters_per_year = random.randint(1, 5)  # Average: 3.3
            bloods_per_year = random.randint(0, 2)

        # Generate 5 years of history
        for year_offset in range(5):
            year_start = self.base_date - timedelta(days=(5 - year_offset) * 365)

            # Encounters for this year
            n_enc = max(0, encounters_per_year + random.randint(-2, 2))
            for _ in range(n_enc):
                enc_date = year_start + timedelta(days=random.randint(0, 364))
                if enc_date < self.base_date:
                    encounters.append(self._generate_encounter(conditions, enc_date))

            # Blood tests for this year
            n_bloods = max(0, bloods_per_year + random.randint(-1, 1))
            for _ in range(n_bloods):
                blood_date = year_start + timedelta(days=random.randint(0, 364))
                if blood_date < self.base_date:
                    blood_results.extend(self._generate_blood_tests(conditions, blood_date))

        encounters.sort(key=lambda e: e["date"])
        blood_results.sort(key=lambda b: b["collected_at"])

        return encounters, blood_results

    def _generate_encounter(self, conditions: List[str], date: datetime) -> dict:
        reasons = ["Routine review", "Medication review", "Blood pressure check",
                   "Chronic disease review", "Acute illness", "Follow-up appointment",
                   "Test results", "Prescription request", "Referral discussion"]

        if conditions:
            reasons.extend([f"{c} review" for c in conditions[:2]])

        channels = ["in-person"] * 6 + ["telephone"] * 3 + ["video"]

        return {
            "date": int(date.timestamp() * 1000),
            "reason": random.choice(reasons),
            "channel": random.choice(channels),
            "text": ""
        }

    def _generate_blood_tests(self, conditions: List[str], date: datetime) -> List[dict]:
        results = []
        timestamp = int(date.timestamp() * 1000)

        # Always include basic panels
        panels_to_run = ["FBC", "U&E"]

        # Add condition-specific tests
        if "Type 2 Diabetes" in conditions:
            panels_to_run.append("HbA1c")
        if "Hypothyroidism" in conditions:
            panels_to_run.append("TFT")
        if "Heart failure" in conditions:
            panels_to_run.append("BNP")
        if random.random() < 0.4:
            panels_to_run.append("Lipids")
        if random.random() < 0.2:
            panels_to_run.append("LFT")

        for panel_name in panels_to_run:
            if panel_name not in BLOOD_PANELS:
                continue

            analytes = []
            for analyte_name, config in BLOOD_PANELS[panel_name].items():
                modifier = 1.0

                # Disease modifiers
                if analyte_name == "eGFR" and "Chronic kidney disease" in conditions:
                    modifier = 0.55
                if analyte_name == "Creatinine" and "Chronic kidney disease" in conditions:
                    modifier = 1.6
                if analyte_name == "HbA1c" and "Type 2 Diabetes" in conditions:
                    modifier = random.choice([1.0, 1.2, 1.4, 1.6])  # Variable control
                if analyte_name == "BNP" and "Heart failure" in conditions:
                    modifier = 4.0

                value = generate_blood_value(config, modifier)

                analytes.append({
                    "name": analyte_name,
                    "value": value,
                    "unit": config["unit"],
                    "ref_low": config["ref_low"],
                    "ref_high": config["ref_high"],
                })

            results.append({
                "panel_id": panel_name.lower(),
                "panel_name": panel_name,
                "collected_at": timestamp,
                "analytes": analytes
            })

        return results

    def _hospital_probability(self, age: int, conditions: List[str]) -> float:
        base = 0.08
        if age > 70: base += 0.1
        if age > 80: base += 0.15
        if "Heart failure" in conditions: base += 0.25
        if "COPD" in conditions: base += 0.2
        if "Previous stroke" in conditions: base += 0.15
        return min(base, 0.7)

    def _generate_hospital_attendances(self, conditions: List[str]) -> List[dict]:
        attendances = []
        complaints = ["Chest pain", "Breathlessness", "Fall", "Collapse", "Palpitations"]

        if "Heart failure" in conditions:
            complaints.extend(["Breathlessness"] * 3 + ["Fluid overload"])
        if "COPD" in conditions:
            complaints.extend(["Breathlessness"] * 2 + ["Chest infection"])

        n = random.randint(1, 4)
        for _ in range(n):
            months_ago = random.randint(1, 36)
            date = self.base_date - timedelta(days=months_ago * 30)

            attendances.append({
                "date": int(date.timestamp() * 1000),
                "presenting_complaint": random.choice(complaints),
                "acuity": random.choices([1, 2, 3, 4], weights=[0.05, 0.25, 0.45, 0.25])[0],
                "outcome": random.choices(["discharged", "admitted"], weights=[0.6, 0.4])[0]
            })

        return sorted(attendances, key=lambda a: a["date"])

    def _get_latest_value(self, blood_results: List[dict], analyte_name: str, default: float) -> float:
        for br in reversed(blood_results):
            for a in br.get("analytes", []):
                if a["name"] == analyte_name:
                    return a["value"]
        return default

    def generate_cohort(self, n_patients: int) -> List[dict]:
        patients = []
        for i in range(n_patients):
            patient = self.generate_patient(f"SYN-{i:06d}")
            patients.append(patient)
            if (i + 1) % 500 == 0:
                print(f"Generated {i + 1}/{n_patients}...")
        return patients


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", "--num-patients", type=int, default=5000)
    parser.add_argument("-o", "--output", default="data/patients_v2.json")
    parser.add_argument("-s", "--seed", type=int, default=42)
    args = parser.parse_args()

    print(f"Generating {args.num_patients} patients with realistic visit frequencies...")
    gen = PatientGenerator(seed=args.seed)
    patients = gen.generate_cohort(args.num_patients)

    # Stats
    n_stroke = sum(1 for p in patients if p["will_have_stroke"])
    n_frequent = sum(1 for p in patients if p["is_frequent_attender"])
    avg_encounters = sum(len(p["encounters"]) for p in patients) / len(patients)
    avg_bloods = sum(len(p["blood_results"]) for p in patients) / len(patients)

    print(f"\n{'='*60}")
    print(f"Total patients: {len(patients)}")
    print(f"Will have stroke: {n_stroke} ({100*n_stroke/len(patients):.1f}%)")
    print(f"Frequent attenders: {n_frequent} ({100*n_frequent/len(patients):.1f}%)")
    print(f"Avg encounters/patient: {avg_encounters:.1f}")
    print(f"Avg blood tests/patient: {avg_bloods:.1f}")

    with open(args.output, "w") as f:
        json.dump(patients, f, indent=2)
    print(f"\nSaved to {args.output}")


if __name__ == "__main__":
    main()
