#!/usr/bin/env python3
"""
Synthetic NHS Patient Generator for Stroke Risk Prediction

Generates patients matching the NHS-SIM schema with:
- Realistic UK prevalence rates for conditions
- Longitudinal blood results over time
- GP encounters and hospital attendances
- 5-year stroke risk labels based on validated scoring

Sources:
- NHS QOF prevalence data
- BHF cardiovascular statistics
- NICE stroke risk guidelines (CHA₂DS₂-VASc)
"""

import json
import random
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional
import hashlib

# =============================================================================
# UK PREVALENCE RATES (NHS QOF / BHF Statistics)
# =============================================================================

# Age-stratified prevalence rates (approximate UK data)
PREVALENCE = {
    # Condition: {age_band: prevalence}
    "Hypertension": {
        "18-39": 0.04, "40-54": 0.15, "55-64": 0.30,
        "65-74": 0.45, "75-84": 0.55, "85+": 0.60
    },
    "Diabetes": {
        "18-39": 0.02, "40-54": 0.06, "55-64": 0.12,
        "65-74": 0.18, "75-84": 0.20, "85+": 0.18
    },
    "Atrial fibrillation": {
        "18-39": 0.001, "40-54": 0.005, "55-64": 0.02,
        "65-74": 0.05, "75-84": 0.10, "85+": 0.15
    },
    "Heart failure": {
        "18-39": 0.001, "40-54": 0.005, "55-64": 0.015,
        "65-74": 0.04, "75-84": 0.08, "85+": 0.12
    },
    "CKD": {
        "18-39": 0.01, "40-54": 0.03, "55-64": 0.06,
        "65-74": 0.12, "75-84": 0.20, "85+": 0.30
    },
    "Previous stroke": {
        "18-39": 0.001, "40-54": 0.005, "55-64": 0.015,
        "65-74": 0.03, "75-84": 0.06, "85+": 0.10
    },
    "Previous TIA": {
        "18-39": 0.0005, "40-54": 0.002, "55-64": 0.008,
        "65-74": 0.015, "75-84": 0.03, "85+": 0.05
    },
    "Peripheral vascular disease": {
        "18-39": 0.001, "40-54": 0.01, "55-64": 0.03,
        "65-74": 0.06, "75-84": 0.10, "85+": 0.12
    },
    "Asthma": {
        "18-39": 0.10, "40-54": 0.08, "55-64": 0.07,
        "65-74": 0.06, "75-84": 0.05, "85+": 0.04
    },
    "COPD": {
        "18-39": 0.001, "40-54": 0.01, "55-64": 0.03,
        "65-74": 0.06, "75-84": 0.08, "85+": 0.08
    },
    "Depression": {
        "18-39": 0.08, "40-54": 0.10, "55-64": 0.08,
        "65-74": 0.06, "75-84": 0.05, "85+": 0.05
    },
    "Anxiety": {
        "18-39": 0.06, "40-54": 0.07, "55-64": 0.05,
        "65-74": 0.04, "75-84": 0.03, "85+": 0.03
    },
    "Rheumatoid arthritis": {
        "18-39": 0.002, "40-54": 0.008, "55-64": 0.012,
        "65-74": 0.015, "75-84": 0.018, "85+": 0.02
    },
    "Osteoarthritis": {
        "18-39": 0.01, "40-54": 0.05, "55-64": 0.12,
        "65-74": 0.20, "75-84": 0.30, "85+": 0.35
    },
    "Hypothyroidism": {
        "18-39": 0.02, "40-54": 0.03, "55-64": 0.05,
        "65-74": 0.07, "75-84": 0.08, "85+": 0.09
    },
    "Obesity": {
        "18-39": 0.20, "40-54": 0.30, "55-64": 0.32,
        "65-74": 0.30, "75-84": 0.25, "85+": 0.18
    },
}

# Smoking prevalence by age (UK data)
SMOKING_PREVALENCE = {
    "18-39": 0.18, "40-54": 0.16, "55-64": 0.14,
    "65-74": 0.10, "75-84": 0.06, "85+": 0.03
}

# =============================================================================
# BLOOD TEST REFERENCE RANGES AND DISTRIBUTIONS
# =============================================================================

BLOOD_PANELS = {
    "FBC": {
        "haemoglobin": {"unit": "g/L", "ref_low": 115, "ref_high": 165, "mean": 140, "std": 12},
        "white_cell_count": {"unit": "×10⁹/L", "ref_low": 4, "ref_high": 11, "mean": 7, "std": 2},
        "platelets": {"unit": "×10⁹/L", "ref_low": 150, "ref_high": 400, "mean": 250, "std": 50},
        "mcv": {"unit": "fL", "ref_low": 80, "ref_high": 100, "mean": 90, "std": 5},
        "neutrophils": {"unit": "×10⁹/L", "ref_low": 2, "ref_high": 7.5, "mean": 4, "std": 1.5},
    },
    "U&E": {
        "sodium": {"unit": "mmol/L", "ref_low": 133, "ref_high": 146, "mean": 140, "std": 3},
        "potassium": {"unit": "mmol/L", "ref_low": 3.5, "ref_high": 5.3, "mean": 4.2, "std": 0.4},
        "urea": {"unit": "mmol/L", "ref_low": 2.5, "ref_high": 7.8, "mean": 5, "std": 1.5},
        "creatinine": {"unit": "µmol/L", "ref_low": 45, "ref_high": 110, "mean": 80, "std": 20},
        "egfr": {"unit": "mL/min/1.73m²", "ref_low": 60, "ref_high": 120, "mean": 90, "std": 20},
    },
    "HbA1c": {
        "hba1c": {"unit": "mmol/mol", "ref_low": 20, "ref_high": 41, "mean": 35, "std": 8},
    },
    "Lipids": {
        "total_cholesterol": {"unit": "mmol/L", "ref_low": 0, "ref_high": 5, "mean": 5.2, "std": 1.0},
        "hdl": {"unit": "mmol/L", "ref_low": 1, "ref_high": 2.5, "mean": 1.4, "std": 0.4},
        "ldl": {"unit": "mmol/L", "ref_low": 0, "ref_high": 3, "mean": 3.0, "std": 0.8},
        "triglycerides": {"unit": "mmol/L", "ref_low": 0, "ref_high": 1.7, "mean": 1.3, "std": 0.6},
    },
    "LFT": {
        "alt": {"unit": "U/L", "ref_low": 0, "ref_high": 40, "mean": 25, "std": 12},
        "alp": {"unit": "U/L", "ref_low": 30, "ref_high": 130, "mean": 70, "std": 25},
        "bilirubin": {"unit": "µmol/L", "ref_low": 0, "ref_high": 21, "mean": 12, "std": 5},
        "albumin": {"unit": "g/L", "ref_low": 35, "ref_high": 50, "mean": 42, "std": 4},
    },
    "CRP": {
        "crp": {"unit": "mg/L", "ref_low": 0, "ref_high": 5, "mean": 3, "std": 4},
    },
    "Coagulation": {
        "inr": {"unit": "", "ref_low": 0.8, "ref_high": 1.2, "mean": 1.0, "std": 0.1},
    },
    "Thyroid": {
        "tsh": {"unit": "mU/L", "ref_low": 0.4, "ref_high": 4.0, "mean": 2.0, "std": 1.0},
    },
    "BNP": {
        "bnp": {"unit": "pg/mL", "ref_low": 0, "ref_high": 100, "mean": 50, "std": 80},
    },
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_age_band(age: int) -> str:
    if age < 40: return "18-39"
    elif age < 55: return "40-54"
    elif age < 65: return "55-64"
    elif age < 75: return "65-74"
    elif age < 85: return "75-84"
    else: return "85+"

def has_condition(age: int, condition: str) -> bool:
    """Determine if patient has condition based on age-stratified prevalence."""
    age_band = get_age_band(age)
    prevalence = PREVALENCE.get(condition, {}).get(age_band, 0)
    return random.random() < prevalence

def generate_blood_value(analyte_config: dict, modifier: float = 1.0) -> float:
    """Generate a blood value with optional disease modifier."""
    mean = analyte_config["mean"] * modifier
    std = analyte_config["std"]
    value = random.gauss(mean, std)
    # Clamp to reasonable physiological range
    min_val = analyte_config["ref_low"] * 0.3
    max_val = analyte_config["ref_high"] * 2.5
    return round(max(min_val, min(max_val, value)), 1)

def calculate_cha2ds2_vasc(
    age: int,
    sex: str,
    heart_failure: bool,
    hypertension: bool,
    diabetes: bool,
    stroke_tia: bool,
    vascular_disease: bool
) -> int:
    """Calculate CHA₂DS₂-VASc score for stroke risk in AFib patients."""
    score = 0
    if heart_failure: score += 1
    if hypertension: score += 1
    if age >= 75: score += 2
    elif age >= 65: score += 1
    if diabetes: score += 1
    if stroke_tia: score += 2
    if vascular_disease: score += 1
    if sex == "F": score += 1
    return score

def calculate_5yr_stroke_risk(
    age: int,
    sex: str,
    conditions: List[str],
    systolic_bp: float,
    smoking: bool,
    hba1c: float,
    total_cholesterol: float,
    hdl: float,
) -> float:
    """
    Estimate 5-year stroke risk based on multiple factors.
    Returns probability 0-1.

    Based on simplified QRISK3 / Framingham stroke risk models.
    """
    # Base risk by age (exponential increase)
    base_risk = 0.001 * math.exp(0.08 * (age - 40))
    base_risk = min(base_risk, 0.15)  # Cap at 15% base

    # Risk multipliers
    multiplier = 1.0

    # Atrial fibrillation - 5x risk
    if "Atrial fibrillation" in conditions:
        multiplier *= 5.0

    # Previous stroke/TIA - 3x risk
    if "Previous stroke" in conditions or "Previous TIA" in conditions:
        multiplier *= 3.0

    # Hypertension - 2x risk
    if "Hypertension" in conditions:
        multiplier *= 2.0

    # Diabetes - 1.5x risk
    if "Diabetes" in conditions:
        multiplier *= 1.5

    # Heart failure - 1.5x risk
    if "Heart failure" in conditions:
        multiplier *= 1.5

    # CKD - 1.3x risk
    if "CKD" in conditions:
        multiplier *= 1.3

    # Peripheral vascular disease - 1.3x risk
    if "Peripheral vascular disease" in conditions:
        multiplier *= 1.3

    # Smoking - 1.5x risk
    if smoking:
        multiplier *= 1.5

    # Sex - males slightly higher risk
    if sex == "M":
        multiplier *= 1.2

    # High BP adds risk
    if systolic_bp > 140:
        multiplier *= 1.0 + (systolic_bp - 140) * 0.01

    # Poor diabetes control
    if hba1c > 53:  # >7% = poor control
        multiplier *= 1.0 + (hba1c - 53) * 0.01

    # Poor cholesterol ratio
    if hdl > 0:
        chol_ratio = total_cholesterol / hdl
        if chol_ratio > 4:
            multiplier *= 1.0 + (chol_ratio - 4) * 0.1

    # Calculate final risk
    risk = base_risk * multiplier
    return min(risk, 0.95)  # Cap at 95%

# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class BloodResult:
    panel_id: str
    panel_name: str
    collected_at: int  # Unix timestamp ms
    analytes: List[Dict]

@dataclass
class Problem:
    code: str
    term: str
    date: str  # YYYY-MM-DD
    status: str  # "active" or "resolved"

@dataclass
class Encounter:
    date: int  # Unix timestamp ms
    reason: str
    channel: str  # "in-person", "telephone", "video"
    text: str

@dataclass
class HospitalAttendance:
    date: int
    presenting_complaint: str
    acuity: int  # 1-5
    outcome: str  # "discharged", "admitted", "left"

@dataclass
class Patient:
    id: str
    age: int
    sex: str
    smoking: bool
    problems: List[Problem] = field(default_factory=list)
    blood_results: List[BloodResult] = field(default_factory=list)
    encounters: List[Encounter] = field(default_factory=list)
    hospital_attendances: List[HospitalAttendance] = field(default_factory=list)
    systolic_bp: float = 120.0
    diastolic_bp: float = 80.0
    bmi: float = 25.0

    # Outcome labels
    stroke_risk_5yr: float = 0.0
    will_have_stroke: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "age": self.age,
            "sex": self.sex,
            "smoking": self.smoking,
            "systolic_bp": self.systolic_bp,
            "diastolic_bp": self.diastolic_bp,
            "bmi": self.bmi,
            "problems": [asdict(p) for p in self.problems],
            "blood_results": [asdict(b) for b in self.blood_results],
            "encounters": [asdict(e) for e in self.encounters],
            "hospital_attendances": [asdict(h) for h in self.hospital_attendances],
            "stroke_risk_5yr": self.stroke_risk_5yr,
            "will_have_stroke": self.will_have_stroke,
        }

    def to_timeline_text(self) -> str:
        """Convert all patient data to a chronological text timeline."""
        events = []

        # Add problems
        for p in self.problems:
            events.append((p.date, f"DIAGNOSIS: {p.term} ({p.status})"))

        # Add blood results
        for br in self.blood_results:
            date_str = datetime.fromtimestamp(br.collected_at / 1000).strftime("%Y-%m-%d")
            abnormals = []
            for a in br.analytes:
                if a["value"] < a["ref_low"]:
                    abnormals.append(f"{a['name']} LOW ({a['value']} {a['unit']})")
                elif a["value"] > a["ref_high"]:
                    abnormals.append(f"{a['name']} HIGH ({a['value']} {a['unit']})")
            if abnormals:
                events.append((date_str, f"BLOODS {br.panel_name}: " + ", ".join(abnormals)))
            else:
                events.append((date_str, f"BLOODS {br.panel_name}: all normal"))

        # Add encounters
        for e in self.encounters:
            date_str = datetime.fromtimestamp(e.date / 1000).strftime("%Y-%m-%d")
            events.append((date_str, f"GP {e.channel}: {e.reason}"))

        # Add hospital attendances
        for h in self.hospital_attendances:
            date_str = datetime.fromtimestamp(h.date / 1000).strftime("%Y-%m-%d")
            events.append((date_str, f"A&E: {h.presenting_complaint} (acuity {h.acuity}) -> {h.outcome}"))

        # Sort by date and format
        events.sort(key=lambda x: x[0])

        header = f"PATIENT {self.id} | Age {self.age} | Sex {self.sex} | Smoker: {self.smoking} | BP: {self.systolic_bp}/{self.diastolic_bp} | BMI: {self.bmi}"
        timeline = "\n".join([f"{date}: {event}" for date, event in events])

        return f"{header}\n{'='*60}\n{timeline}"

# =============================================================================
# PATIENT GENERATOR
# =============================================================================

class PatientGenerator:
    def __init__(self, seed: int = 42):
        random.seed(seed)
        self.base_date = datetime(2026, 9, 1)  # Current sim date

    def generate_patient(self, patient_id: str) -> Patient:
        """Generate a single synthetic patient with realistic data."""

        # Demographics - UK age distribution weighted toward older (healthcare users)
        age = self._generate_age()
        sex = random.choice(["M", "F"])
        age_band = get_age_band(age)

        # Smoking status
        smoking = random.random() < SMOKING_PREVALENCE.get(age_band, 0.1)

        # Generate conditions based on prevalence
        conditions = []
        for condition in PREVALENCE.keys():
            if has_condition(age, condition):
                conditions.append(condition)

        # Add correlated conditions (comorbidities cluster)
        conditions = self._add_comorbidities(conditions, age)

        # Generate vitals
        systolic_bp = self._generate_bp(conditions, age)
        diastolic_bp = systolic_bp * random.uniform(0.55, 0.65)
        bmi = self._generate_bmi(conditions, age)

        # Create patient
        patient = Patient(
            id=patient_id,
            age=age,
            sex=sex,
            smoking=smoking,
            systolic_bp=round(systolic_bp, 0),
            diastolic_bp=round(diastolic_bp, 0),
            bmi=round(bmi, 1),
        )

        # Generate problem list
        patient.problems = self._generate_problems(conditions, age)

        # Generate blood results (multiple time points over 3 years)
        patient.blood_results = self._generate_blood_results(conditions, age)

        # Generate GP encounters
        patient.encounters = self._generate_encounters(conditions, age)

        # Generate hospital attendances (some patients)
        if random.random() < self._hospital_probability(age, conditions):
            patient.hospital_attendances = self._generate_hospital_attendances(conditions)

        # Calculate stroke risk
        condition_names = [p.term for p in patient.problems]

        # Get latest blood values for risk calculation
        hba1c = 35.0
        total_chol = 5.0
        hdl = 1.4
        for br in patient.blood_results:
            for a in br.analytes:
                if a["name"] == "HbA1c":
                    hba1c = a["value"]
                elif a["name"] == "Total cholesterol":
                    total_chol = a["value"]
                elif a["name"] == "HDL cholesterol":
                    hdl = a["value"]

        patient.stroke_risk_5yr = calculate_5yr_stroke_risk(
            age=age,
            sex=sex,
            conditions=condition_names,
            systolic_bp=systolic_bp,
            smoking=smoking,
            hba1c=hba1c,
            total_cholesterol=total_chol,
            hdl=hdl,
        )

        # Binary outcome - will they have a stroke in 5 years?
        # Use risk as probability with some randomness
        patient.will_have_stroke = random.random() < patient.stroke_risk_5yr

        return patient

    def _generate_age(self) -> int:
        """Generate age weighted toward older adults (healthcare utilizers)."""
        # Weighted distribution - more older patients
        weights = [
            (18, 39, 0.15),
            (40, 54, 0.20),
            (55, 64, 0.20),
            (65, 74, 0.25),
            (75, 84, 0.15),
            (85, 95, 0.05),
        ]
        r = random.random()
        cumulative = 0
        for low, high, weight in weights:
            cumulative += weight
            if r < cumulative:
                return random.randint(low, high)
        return 70

    def _add_comorbidities(self, conditions: List[str], age: int) -> List[str]:
        """Add correlated comorbidities - diseases cluster together."""
        new_conditions = conditions.copy()

        # Metabolic syndrome cluster
        if "Diabetes" in conditions or "Obesity" in conditions:
            if random.random() < 0.5 and "Hypertension" not in new_conditions:
                new_conditions.append("Hypertension")

        # Cardiovascular cluster
        if "Hypertension" in conditions and age > 60:
            if random.random() < 0.2 and "Heart failure" not in new_conditions:
                new_conditions.append("Heart failure")
            if random.random() < 0.15 and "Atrial fibrillation" not in new_conditions:
                new_conditions.append("Atrial fibrillation")

        # AFib and stroke
        if "Atrial fibrillation" in conditions:
            if random.random() < 0.1 and "Previous stroke" not in new_conditions:
                new_conditions.append("Previous stroke")

        # CKD and cardiovascular
        if "CKD" in conditions:
            if random.random() < 0.4 and "Hypertension" not in new_conditions:
                new_conditions.append("Hypertension")

        return new_conditions

    def _generate_bp(self, conditions: List[str], age: int) -> float:
        """Generate realistic blood pressure based on conditions."""
        base_systolic = 110 + age * 0.3

        if "Hypertension" in conditions:
            # Controlled vs uncontrolled
            if random.random() < 0.6:  # 60% controlled
                base_systolic = random.gauss(135, 10)
            else:
                base_systolic = random.gauss(155, 15)
        else:
            base_systolic = random.gauss(base_systolic, 10)

        return max(90, min(200, base_systolic))

    def _generate_bmi(self, conditions: List[str], age: int) -> float:
        """Generate realistic BMI."""
        if "Obesity" in conditions:
            return random.gauss(33, 5)
        elif "Diabetes" in conditions:
            return random.gauss(29, 4)
        else:
            return random.gauss(26, 4)

    def _generate_problems(self, conditions: List[str], age: int) -> List[Problem]:
        """Generate problem list with realistic dates."""
        problems = []

        for i, condition in enumerate(conditions):
            # Random onset date in past 1-10 years
            years_ago = random.randint(1, max(1, min(10, age - 18)))
            onset_date = self.base_date - timedelta(days=years_ago * 365 + random.randint(0, 365))

            problems.append(Problem(
                code=f"SIM-COND-{i+1:04d}",
                term=condition,
                date=onset_date.strftime("%Y-%m-%d"),
                status="active" if random.random() < 0.85 else "resolved"
            ))

        # Add some routine entries
        for j in range(random.randint(2, 5)):
            routine_items = ["Medication review", "Preventive health review",
                           "Annual review", "Follow-up", "Routine blood test"]
            years_ago = random.uniform(0.5, 3)
            date = self.base_date - timedelta(days=int(years_ago * 365))

            problems.append(Problem(
                code=f"SIM-ROUTINE-{j+1:04d}",
                term=random.choice(routine_items),
                date=date.strftime("%Y-%m-%d"),
                status="active"
            ))

        return sorted(problems, key=lambda p: p.date)

    def _generate_blood_results(self, conditions: List[str], age: int) -> List[BloodResult]:
        """Generate longitudinal blood results over 3 years."""
        results = []

        # Number of blood test sets (more if chronic conditions)
        n_tests = random.randint(2, 4)
        if any(c in conditions for c in ["Diabetes", "CKD", "Heart failure"]):
            n_tests = random.randint(4, 8)

        for i in range(n_tests):
            # Time point
            months_ago = random.randint(1, 36)
            test_date = self.base_date - timedelta(days=months_ago * 30)
            timestamp = int(test_date.timestamp() * 1000)

            # Generate panels based on conditions
            panels_to_generate = ["FBC", "U&E"]

            if "Diabetes" in conditions:
                panels_to_generate.append("HbA1c")
            if random.random() < 0.5:
                panels_to_generate.append("Lipids")
            if "Heart failure" in conditions:
                panels_to_generate.append("BNP")
            if random.random() < 0.3:
                panels_to_generate.append("LFT")
            if random.random() < 0.2:
                panels_to_generate.append("CRP")

            for panel_id in panels_to_generate:
                if panel_id not in BLOOD_PANELS:
                    continue

                analytes = []
                for analyte_name, config in BLOOD_PANELS[panel_id].items():
                    # Apply disease modifiers
                    modifier = 1.0

                    if analyte_name == "egfr" and "CKD" in conditions:
                        modifier = 0.6  # Reduced eGFR
                    if analyte_name == "creatinine" and "CKD" in conditions:
                        modifier = 1.5  # Elevated creatinine
                    if analyte_name == "hba1c" and "Diabetes" in conditions:
                        modifier = 1.4  # Elevated HbA1c
                    if analyte_name == "bnp" and "Heart failure" in conditions:
                        modifier = 4.0  # Elevated BNP
                    if analyte_name == "crp" and random.random() < 0.2:
                        modifier = 3.0  # Occasional inflammation

                    value = generate_blood_value(config, modifier)

                    analytes.append({
                        "name": analyte_name.replace("_", " ").title(),
                        "value": value,
                        "unit": config["unit"],
                        "ref_low": config["ref_low"],
                        "ref_high": config["ref_high"],
                    })

                results.append(BloodResult(
                    panel_id=panel_id.lower(),
                    panel_name=panel_id,
                    collected_at=timestamp,
                    analytes=analytes
                ))

        return sorted(results, key=lambda r: r.collected_at)

    def _generate_encounters(self, conditions: List[str], age: int) -> List[Encounter]:
        """Generate GP encounters."""
        encounters = []

        # More encounters if more conditions
        n_encounters = random.randint(2, 4) + len(conditions)
        n_encounters = min(n_encounters, 15)

        reasons = [
            "Routine review", "Medication review", "Blood pressure check",
            "Chronic disease review", "Acute illness", "Follow-up",
            "Test results discussion", "Referral discussion"
        ]

        channels = ["in-person"] * 5 + ["telephone"] * 3 + ["video"]

        for i in range(n_encounters):
            months_ago = random.randint(1, 36)
            enc_date = self.base_date - timedelta(days=months_ago * 30)

            encounters.append(Encounter(
                date=int(enc_date.timestamp() * 1000),
                reason=random.choice(reasons),
                channel=random.choice(channels),
                text=f"Routine consultation regarding {random.choice(conditions) if conditions else 'general health'}."
            ))

        return sorted(encounters, key=lambda e: e.date)

    def _hospital_probability(self, age: int, conditions: List[str]) -> float:
        """Calculate probability of hospital attendance."""
        base = 0.1
        if age > 70: base += 0.1
        if age > 80: base += 0.1
        if "Heart failure" in conditions: base += 0.2
        if "COPD" in conditions: base += 0.15
        if "Previous stroke" in conditions: base += 0.15
        return min(base, 0.6)

    def _generate_hospital_attendances(self, conditions: List[str]) -> List[HospitalAttendance]:
        """Generate A&E attendances."""
        attendances = []

        complaints = [
            "Chest pain", "Breathlessness", "Fall", "Confusion",
            "Abdominal pain", "Collapse", "Palpitations", "Weakness"
        ]

        # Weight complaints based on conditions
        if "Heart failure" in conditions:
            complaints.extend(["Breathlessness"] * 3)
        if "Atrial fibrillation" in conditions:
            complaints.extend(["Palpitations"] * 2)
        if "Previous stroke" in conditions:
            complaints.extend(["Weakness", "Confusion"])

        n_attendances = random.randint(1, 3)

        for i in range(n_attendances):
            months_ago = random.randint(1, 24)
            att_date = self.base_date - timedelta(days=months_ago * 30)

            attendances.append(HospitalAttendance(
                date=int(att_date.timestamp() * 1000),
                presenting_complaint=random.choice(complaints),
                acuity=random.choices([1, 2, 3, 4, 5], weights=[0.05, 0.2, 0.4, 0.25, 0.1])[0],
                outcome=random.choices(
                    ["discharged", "admitted", "left"],
                    weights=[0.6, 0.35, 0.05]
                )[0]
            ))

        return sorted(attendances, key=lambda a: a.date)

    def generate_cohort(self, n_patients: int, start_id: int = 0) -> List[Patient]:
        """Generate a cohort of patients."""
        patients = []
        for i in range(n_patients):
            patient_id = f"SYN-{start_id + i:06d}"
            patient = self.generate_patient(patient_id)
            patients.append(patient)

            if (i + 1) % 500 == 0:
                print(f"Generated {i + 1}/{n_patients} patients...")

        return patients


def main():
    """Generate synthetic patient cohort and save to files."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate synthetic NHS patients")
    parser.add_argument("-n", "--num-patients", type=int, default=5000,
                       help="Number of patients to generate")
    parser.add_argument("-o", "--output", type=str, default="patients.json",
                       help="Output JSON file")
    parser.add_argument("--timelines", type=str, default="timelines.txt",
                       help="Output text timelines file")
    parser.add_argument("-s", "--seed", type=int, default=42,
                       help="Random seed")

    args = parser.parse_args()

    print(f"Generating {args.num_patients} synthetic patients...")
    generator = PatientGenerator(seed=args.seed)
    patients = generator.generate_cohort(args.num_patients)

    # Statistics
    n_stroke = sum(1 for p in patients if p.will_have_stroke)
    n_afib = sum(1 for p in patients if any(pr.term == "Atrial fibrillation" for pr in p.problems))
    n_hypertension = sum(1 for p in patients if any(pr.term == "Hypertension" for pr in p.problems))
    n_diabetes = sum(1 for p in patients if any(pr.term == "Diabetes" for pr in p.problems))

    print(f"\n{'='*60}")
    print(f"COHORT STATISTICS")
    print(f"{'='*60}")
    print(f"Total patients: {len(patients)}")
    print(f"Will have stroke in 5yr: {n_stroke} ({100*n_stroke/len(patients):.1f}%)")
    print(f"Atrial fibrillation: {n_afib} ({100*n_afib/len(patients):.1f}%)")
    print(f"Hypertension: {n_hypertension} ({100*n_hypertension/len(patients):.1f}%)")
    print(f"Diabetes: {n_diabetes} ({100*n_diabetes/len(patients):.1f}%)")
    print(f"Mean age: {sum(p.age for p in patients)/len(patients):.1f}")
    print(f"Mean 5yr stroke risk: {100*sum(p.stroke_risk_5yr for p in patients)/len(patients):.2f}%")

    # Save JSON
    print(f"\nSaving to {args.output}...")
    with open(args.output, "w") as f:
        json.dump([p.to_dict() for p in patients], f, indent=2)

    # Save text timelines
    print(f"Saving timelines to {args.timelines}...")
    with open(args.timelines, "w") as f:
        for p in patients:
            f.write(p.to_timeline_text())
            f.write(f"\n\n5-YEAR STROKE RISK: {p.stroke_risk_5yr*100:.1f}%")
            f.write(f"\nOUTCOME: {'STROKE' if p.will_have_stroke else 'NO STROKE'}")
            f.write("\n" + "="*80 + "\n\n")

    print("Done!")


if __name__ == "__main__":
    main()
