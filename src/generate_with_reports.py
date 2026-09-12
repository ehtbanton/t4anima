#!/usr/bin/env python3
"""
Generate patients with full narrative text reports.
"""

import json
import random
import math
from datetime import datetime, timedelta

PREVALENCE = {
    "Hypertension": {"18-39": 0.04, "40-54": 0.15, "55-64": 0.30, "65-74": 0.45, "75+": 0.55},
    "Type 2 Diabetes": {"18-39": 0.02, "40-54": 0.06, "55-64": 0.12, "65-74": 0.18, "75+": 0.19},
    "Atrial fibrillation": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.02, "65-74": 0.05, "75+": 0.12},
    "Heart failure": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.015, "65-74": 0.04, "75+": 0.10},
    "CKD Stage 3+": {"18-39": 0.01, "40-54": 0.03, "55-64": 0.06, "65-74": 0.12, "75+": 0.25},
    "Previous stroke": {"18-39": 0.001, "40-54": 0.005, "55-64": 0.015, "65-74": 0.03, "75+": 0.08},
    "Previous TIA": {"18-39": 0.0005, "40-54": 0.002, "55-64": 0.008, "65-74": 0.015, "75+": 0.04},
    "Hyperlipidaemia": {"18-39": 0.02, "40-54": 0.10, "55-64": 0.20, "65-74": 0.25, "75+": 0.22},
}

SMOKING = {"18-39": 0.16, "40-54": 0.14, "55-64": 0.12, "65-74": 0.08, "75+": 0.04}

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


class ReportGenerator:
    """Generate realistic NHS clinical report narratives."""

    def __init__(self):
        self.doctors = ["Dr S Patel", "Dr J Williams", "Dr A Khan", "Dr M O'Brien", "Dr R Singh",
                       "Dr E Thompson", "Dr L Chen", "Dr K Murphy", "Dr D Brown", "Dr F Ahmed"]

    def fbc(self, patient, conditions, abnormal=False):
        hb = random.randint(95, 110) if abnormal else random.randint(130, 165)
        wcc = random.uniform(11.5, 15.0) if abnormal else random.uniform(4.5, 10.0)
        plt = random.randint(100, 140) if abnormal else random.randint(150, 380)
        mcv = random.randint(70, 78) if abnormal else random.randint(82, 98)

        text = f"""FULL BLOOD COUNT

Haemoglobin: {hb} g/L (ref: 130-170)
White Cell Count: {wcc:.1f} x10^9/L (ref: 4.0-11.0)
Platelets: {plt} x10^9/L (ref: 150-400)
MCV: {mcv} fL (ref: 80-100)
MCH: {random.randint(27, 32)} pg
MCHC: {random.randint(310, 350)} g/L
RDW: {random.uniform(11, 15):.1f}%

"""
        if abnormal:
            if hb < 120:
                text += f"COMMENT: Low haemoglobin noted. {'Consider iron studies and B12/folate if not recently checked. ' if mcv < 80 else 'Normocytic picture - consider chronic disease. '}"
            if wcc > 11:
                text += "Raised white cell count - correlate clinically. "
        else:
            text += "COMMENT: All parameters within normal limits."

        text += f"\n\nReported by: {random.choice(self.doctors)}"
        return text

    def uande(self, patient, conditions, abnormal=False):
        has_ckd = any("ckd" in c.lower() for c in conditions)
        na = random.randint(128, 132) if abnormal else random.randint(136, 144)
        k = random.uniform(5.5, 6.2) if abnormal else random.uniform(3.8, 5.0)
        cr = random.randint(150, 220) if (abnormal or has_ckd) else random.randint(60, 110)
        egfr = max(15, int(90 - (cr - 80) * 0.5))
        urea = random.uniform(8, 15) if abnormal else random.uniform(2.5, 7.0)

        text = f"""UREA AND ELECTROLYTES

Sodium: {na} mmol/L (ref: 133-146)
Potassium: {k:.1f} mmol/L (ref: 3.5-5.3)
Urea: {urea:.1f} mmol/L (ref: 2.5-7.8)
Creatinine: {cr} umol/L (ref: 45-120)
eGFR: {egfr} mL/min/1.73m2

"""
        if k > 5.3:
            text += "ALERT: Hyperkalaemia - please review medications (ACEi/ARB/spironolactone) and repeat urgently if symptomatic.\n"
        if egfr < 60:
            stage = "3a" if egfr >= 45 else "3b" if egfr >= 30 else "4" if egfr >= 15 else "5"
            text += f"COMMENT: eGFR {egfr} consistent with CKD Stage {stage}. "
            if egfr < 30:
                text += "Consider nephrology referral. "
        if na < 133:
            text += "Hyponatraemia noted - assess fluid status and medication review. "
        if not abnormal and egfr >= 60:
            text += "COMMENT: Renal function within normal limits."

        text += f"\n\nReported by: {random.choice(self.doctors)}"
        return text

    def hba1c(self, patient, conditions, abnormal=False):
        has_dm = any("diabetes" in c.lower() for c in conditions)
        if has_dm:
            hba1c = random.randint(58, 86) if abnormal else random.randint(48, 58)
        else:
            hba1c = random.randint(42, 47) if not abnormal else random.randint(48, 55)

        pct = round(hba1c * 0.0915 + 2.15, 1)

        text = f"""HbA1c

HbA1c: {hba1c} mmol/mol ({pct}%)
Reference: <48 mmol/mol (non-diabetic)
           48-58 mmol/mol (well-controlled T2DM)

"""
        if has_dm:
            if hba1c > 75:
                text += f"COMMENT: Significantly elevated HbA1c at {hba1c} mmol/mol indicates poor glycaemic control. Recommend medication review and consideration of treatment intensification per NICE NG28. Lifestyle reinforcement advised."
            elif hba1c > 58:
                text += f"COMMENT: HbA1c above target. Consider optimisation of current therapy. Ensure annual diabetic review completed including retinal screening and foot check."
            else:
                text += "COMMENT: HbA1c at target for patient with established Type 2 Diabetes. Continue current management."
        else:
            if hba1c >= 48:
                text += f"COMMENT: HbA1c {hba1c} mmol/mol in diagnostic range for Type 2 Diabetes (>=48). Recommend fasting glucose and repeat HbA1c to confirm. If confirmed, initiate diabetes pathway."
            elif hba1c >= 42:
                text += "COMMENT: HbA1c in pre-diabetic range (42-47). Lifestyle advice regarding diet and exercise. Repeat in 12 months."
            else:
                text += "COMMENT: HbA1c within normal non-diabetic range."

        text += f"\n\nReported by: {random.choice(self.doctors)}"
        return text

    def lipids(self, patient, conditions, abnormal=False):
        tc = random.uniform(6.0, 8.5) if abnormal else random.uniform(3.8, 5.0)
        hdl = random.uniform(0.8, 1.1) if abnormal else random.uniform(1.2, 1.8)
        tg = random.uniform(2.5, 4.0) if abnormal else random.uniform(0.8, 2.0)
        ldl = tc - hdl - (tg * 0.45)
        ratio = tc / hdl

        text = f"""LIPID PROFILE

Total Cholesterol: {tc:.1f} mmol/L (target: <5.0)
HDL Cholesterol: {hdl:.1f} mmol/L (target: >1.0)
LDL Cholesterol: {ldl:.1f} mmol/L (target: <3.0)
Triglycerides: {tg:.1f} mmol/L (target: <2.3)
TC:HDL Ratio: {ratio:.1f} (target: <4.5)

"""
        qrisk = random.randint(8, 25) if abnormal else random.randint(2, 8)

        if tc > 7.5 or ldl > 4.9:
            text += f"COMMENT: Significantly elevated cholesterol. Consider familial hypercholesterolaemia if LDL >4.9 with family history. QRISK3 score: {qrisk}%. "
            if qrisk >= 10:
                text += "Statin therapy indicated per NICE CG181. Discuss with patient."
        elif tc > 5.0 or ldl > 3.0:
            text += f"COMMENT: Lipids above optimal range. QRISK3 score: {qrisk}%. "
            if qrisk >= 10:
                text += "Consider statin therapy - QRISK >10% threshold met."
            else:
                text += "Lifestyle modification advised. Repeat in 12 months."
        else:
            text += "COMMENT: Lipid profile within target range."

        text += f"\n\nReported by: {random.choice(self.doctors)}"
        return text

    def ecg(self, patient, conditions, abnormal=False):
        has_af = any("atrial" in c.lower() or "fibrillation" in c.lower() for c in conditions)

        if has_af or (abnormal and random.random() < 0.5):
            rate = random.randint(85, 140)
            rhythm = "Atrial fibrillation"
            text = f"""12-LEAD ELECTROCARDIOGRAM

Rate: {rate} bpm (irregularly irregular)
Rhythm: {rhythm}
Axis: Normal
PR interval: Not measurable (AF)
QRS duration: {random.randint(80, 110)} ms
QTc: {random.randint(380, 450)} ms

INTERPRETATION:
Atrial fibrillation with {"rapid" if rate > 100 else "controlled"} ventricular response.
{"No" if random.random() > 0.3 else "Possible"} ST/T wave changes.
{"Consider rate control and anticoagulation assessment (CHA2DS2-VASc)." if rate > 100 else "Rate reasonably controlled."}
"""
        elif abnormal:
            rate = random.randint(95, 120)
            text = f"""12-LEAD ELECTROCARDIOGRAM

Rate: {rate} bpm
Rhythm: Sinus tachycardia
Axis: {random.choice(["Normal", "Left axis deviation"])}
PR interval: {random.randint(140, 200)} ms
QRS duration: {random.randint(80, 110)} ms
QTc: {random.randint(400, 480)} ms

INTERPRETATION:
Sinus tachycardia. {random.choice(["Nonspecific ST-T wave changes in lateral leads.", "T wave inversion in V1-V2, may be normal variant.", "Borderline LVH by voltage criteria."])}
Clinical correlation recommended.
"""
        else:
            rate = random.randint(58, 85)
            text = f"""12-LEAD ELECTROCARDIOGRAM

Rate: {rate} bpm
Rhythm: Normal sinus rhythm
Axis: Normal ({random.randint(30, 75)} degrees)
PR interval: {random.randint(140, 180)} ms
QRS duration: {random.randint(80, 100)} ms
QTc: {random.randint(380, 430)} ms

INTERPRETATION:
Normal ECG. Sinus rhythm, normal axis, no acute ischaemic changes.
No evidence of conduction abnormality.
"""
        text += f"\nReported by: {random.choice(self.doctors)}"
        return text

    def echo(self, patient, conditions, abnormal=False):
        has_hf = any("heart failure" in c.lower() for c in conditions)

        if has_hf or abnormal:
            ef = random.randint(28, 45)
            lv = "moderately impaired" if ef < 40 else "mildly impaired"
            text = f"""TRANSTHORACIC ECHOCARDIOGRAM

LEFT VENTRICLE:
- LV internal diameter (diastole): {random.randint(52, 62)} mm (ref: 39-53)
- Ejection fraction: {ef}% (ref: >55%)
- LV systolic function: {lv.title()}
- Wall motion: {"Global hypokinesis" if ef < 35 else "Basal inferior/inferolateral hypokinesis"}
- LV wall thickness: {random.randint(10, 14)} mm

LEFT ATRIUM:
- LA diameter: {random.randint(40, 48)} mm ({"dilated" if True else "normal"})

RIGHT HEART:
- RV function: {"Mildly impaired" if random.random() < 0.3 else "Normal"}
- TAPSE: {random.randint(16, 22)} mm
- Estimated PASP: {random.randint(30, 45)} mmHg

VALVES:
- Mitral valve: {random.choice(["Mild MR", "Moderate MR", "Trivial MR"])}
- Aortic valve: {random.choice(["No significant AS/AR", "Mild AS", "Sclerotic, no stenosis"])}
- Tricuspid valve: {random.choice(["Mild TR", "Trivial TR"])}

CONCLUSION:
{lv.title()} LV systolic function with EF {ef}%. {random.choice(["Dilated LV cavity.", "Mild LV dilatation.", ""])}
{"Consider optimisation of heart failure therapy." if ef < 40 else "Stable appearances compared to previous."}
"""
        else:
            ef = random.randint(55, 68)
            text = f"""TRANSTHORACIC ECHOCARDIOGRAM

LEFT VENTRICLE:
- LV internal diameter (diastole): {random.randint(42, 52)} mm (ref: 39-53)
- Ejection fraction: {ef}% (ref: >55%)
- LV systolic function: Normal
- Wall motion: Normal
- LV wall thickness: {random.randint(8, 11)} mm

LEFT ATRIUM:
- LA diameter: {random.randint(32, 40)} mm (normal)

RIGHT HEART:
- RV function: Normal
- TAPSE: {random.randint(20, 26)} mm
- No evidence of pulmonary hypertension

VALVES:
- Mitral valve: No significant regurgitation
- Aortic valve: Trileaflet, no stenosis
- Tricuspid valve: Trivial TR (physiological)

CONCLUSION:
Normal echocardiogram. Preserved LV systolic function.
No significant valvular abnormality.
"""
        text += f"\nReported by: {random.choice(self.doctors)}"
        return text

    def ct_head(self, patient, conditions, abnormal=False):
        has_stroke = any("stroke" in c.lower() for c in conditions)

        if has_stroke and random.random() < 0.7:
            text = f"""CT HEAD (Non-contrast)

CLINICAL INDICATION: ?Stroke / Neurological deficit

TECHNIQUE: Axial non-contrast CT of the brain.

FINDINGS:
- {"Established infarct in the left MCA territory with associated encephalomalacia." if random.random() < 0.5 else "Small vessel ischaemic changes in the periventricular white matter."}
- {"Old lacunar infarct in the right basal ganglia." if random.random() < 0.4 else ""}
- Ventricles: Normal size, no hydrocephalus
- No acute intracranial haemorrhage
- No midline shift or mass effect
- {random.choice(["Mild generalised cerebral atrophy appropriate for age.", "No significant atrophy.", "Moderate small vessel disease."])}
- Calvarium and visualised paranasal sinuses: Unremarkable

CONCLUSION:
{"Evidence of previous cerebrovascular disease as described." if random.random() < 0.6 else "No acute intracranial abnormality."}
{"Background small vessel ischaemic changes." if random.random() < 0.5 else ""}
"""
        elif abnormal:
            text = f"""CT HEAD (Non-contrast)

CLINICAL INDICATION: Headache / Dizziness / Falls

TECHNIQUE: Axial non-contrast CT of the brain.

FINDINGS:
- {random.choice(["Scattered white matter hypodensities consistent with chronic small vessel ischaemia.", "Mild periventricular white matter changes.", "No focal lesion identified."])}
- Ventricles: {random.choice(["Normal size", "Mildly prominent, likely involutional"])}
- No intracranial haemorrhage
- No midline shift
- {random.choice(["Generalised cerebral atrophy", "Age-appropriate appearances", "Mild cerebral atrophy"])}
- Incidental note: {random.choice(["Mucosal thickening in the maxillary sinuses.", "Calcified pineal gland (normal variant).", "No relevant incidental findings."])}

CONCLUSION:
No acute intracranial pathology. Age-related involutional changes.
"""
        else:
            text = f"""CT HEAD (Non-contrast)

CLINICAL INDICATION: Exclude intracranial pathology

TECHNIQUE: Axial non-contrast CT of the brain.

FINDINGS:
- Brain parenchyma: Normal grey-white matter differentiation
- Ventricles: Normal size and configuration
- No intracranial haemorrhage
- No midline shift or mass effect
- No extra-axial collection
- Basal cisterns: Patent
- Calvarium: Intact

CONCLUSION:
Normal CT head. No acute intracranial abnormality.
"""
        text += f"\nReported by: {random.choice(self.doctors)}"
        return text

    def carotid_doppler(self, patient, conditions, abnormal=False):
        has_stroke = any("stroke" in c.lower() or "tia" in c.lower() for c in conditions)

        if abnormal or has_stroke:
            r_stenosis = random.randint(40, 75)
            l_stenosis = random.randint(20, 50)
            text = f"""CAROTID DOPPLER ULTRASOUND

CLINICAL INDICATION: {"Previous stroke/TIA" if has_stroke else "Cardiovascular risk assessment"}

FINDINGS:

RIGHT CAROTID:
- Common carotid artery: Patent, {"mild" if random.random() < 0.5 else "moderate"} intimal thickening
- Internal carotid artery: {r_stenosis}% stenosis (NASCET criteria)
  - PSV: {120 + r_stenosis * 2} cm/s
  - Plaque: {"Calcified" if random.random() < 0.5 else "Mixed echogenicity"}, {"ulcerated surface" if r_stenosis > 60 else "smooth surface"}
- External carotid artery: Patent

LEFT CAROTID:
- Common carotid artery: Patent, mild intimal thickening
- Internal carotid artery: {l_stenosis}% stenosis (NASCET criteria)
  - PSV: {80 + l_stenosis * 1.5:.0f} cm/s
  - Plaque: Calcified, smooth surface
- External carotid artery: Patent

VERTEBRAL ARTERIES:
- Bilateral antegrade flow demonstrated

CONCLUSION:
{"Significant" if r_stenosis >= 50 else "Moderate"} right ICA stenosis at {r_stenosis}%.
{"Consider vascular surgery referral if symptomatic." if r_stenosis >= 50 else ""}
{"Left ICA shows mild-moderate disease." if l_stenosis >= 30 else "Left ICA minimal disease."}
"""
        else:
            text = f"""CAROTID DOPPLER ULTRASOUND

CLINICAL INDICATION: Cardiovascular risk screening

FINDINGS:

RIGHT CAROTID:
- Common carotid artery: Patent, minimal intimal thickening
- Internal carotid artery: <30% stenosis, no significant plaque
- External carotid artery: Patent
- PSV within normal limits

LEFT CAROTID:
- Common carotid artery: Patent, no significant plaque
- Internal carotid artery: <30% stenosis
- External carotid artery: Patent

VERTEBRAL ARTERIES:
- Bilateral antegrade flow demonstrated

CONCLUSION:
No haemodynamically significant carotid stenosis bilaterally.
Minimal atherosclerotic disease only.
"""
        text += f"\nReported by: {random.choice(self.doctors)}"
        return text

    def gp_consultation(self, patient, conditions, reason):
        bp = f"{patient['sbp']}/{patient['dbp']}"

        templates = [
            f"""GP CONSULTATION NOTE

Presenting complaint: {reason}

History: Patient attended for {reason.lower()}. {"Reports compliance with medications." if conditions else "No significant past medical history."}
{"Currently on " + ", ".join(conditions[:2]) + " management." if conditions else ""}
No new symptoms of concern.

Examination:
- General: Well, comfortable at rest
- BP: {bp} mmHg {"(above target - discussed lifestyle)" if patient['sbp'] > 140 else "(satisfactory)"}
- Pulse: {random.randint(62, 88)} bpm, regular
- BMI: {random.uniform(22, 32):.1f}

Plan:
- {"Continue current medications" if conditions else "Reassurance given"}
- {"Lifestyle advice reinforced" if patient['sbp'] > 140 or patient['smoking'] else ""}
- Routine follow-up {"in 3 months" if conditions else "as needed"}
""",
            f"""GP CONSULTATION

S: {reason}. Patient reports {"stable symptoms" if conditions else "no concerns"}. {"Taking medications as prescribed." if conditions else ""}

O:
- Obs: BP {bp}, HR {random.randint(64, 85)}, Sats {random.randint(96, 99)}% RA
- Exam: {"NAD" if random.random() < 0.7 else "Mild peripheral oedema"}

A: {"Chronic disease review - stable" if conditions else "Well patient"}

P:
- {"Repeat prescription issued" if conditions else "No action required"}
- {"Bloods requested for annual review" if random.random() < 0.3 else ""}
- F/U {"3/12" if conditions else "PRN"}
"""
        ]
        return random.choice(templates) + f"\nSeen by: {random.choice(self.doctors)}"

    def ae_attendance(self, patient, conditions, complaint, outcome):
        text = f"""EMERGENCY DEPARTMENT ATTENDANCE

TRIAGE: {random.choice(["Category 3 - Urgent", "Category 4 - Standard", "Category 2 - Very Urgent"])}

PRESENTING COMPLAINT: {complaint}

HISTORY OF PRESENTING COMPLAINT:
Patient presented with {complaint.lower()}. {"Onset " + random.choice(["sudden", "gradual", "this morning", "over the past 2 days"]) + "."}
{"Associated symptoms: " + random.choice(["mild nausea", "dizziness", "SOB on exertion", "no associated symptoms"]) + "."}
{"PMH includes " + ", ".join(conditions[:2]) + "." if conditions else "No significant PMH."}

OBSERVATIONS:
- BP: {patient['sbp']}/{patient['dbp']} mmHg
- HR: {random.randint(72, 110)} bpm
- RR: {random.randint(14, 20)} /min
- SpO2: {random.randint(94, 99)}% on air
- Temp: {random.uniform(36.2, 37.8):.1f}°C
- GCS: 15/15

EXAMINATION:
{random.choice([
    "Cardiovascular: HS I+II+0, no murmurs. Chest clear.",
    "Neuro: Grossly intact. No focal deficit. PEARL.",
    "General: Alert, comfortable. No acute distress.",
])}

INVESTIGATIONS:
- ECG: {"Sinus rhythm" if random.random() < 0.7 else "AF with controlled rate"}
- Bloods: FBC, U&E - {"normal" if random.random() < 0.6 else "mildly deranged, see results"}

IMPRESSION:
{random.choice([
    "Likely " + complaint.lower().replace("a&e: ", "") + " - benign presentation",
    "? Cardiac cause - low risk features",
    "Non-specific symptoms - no red flags",
])}

PLAN/OUTCOME: {outcome}
{"GP follow-up advised." if "Discharged" in outcome else "Admitted under medical team."}
"""
        text += f"\nSeen by: {random.choice(self.doctors)}"
        return text

    def generate_report(self, event, patient, conditions):
        """Generate full text report based on event type."""
        report_type = event.get('report_type', event.get('title', ''))
        abnormal = 'abnormal' in event.get('detail', '').lower()

        generators = {
            'FBC': self.fbc,
            'Full Blood Count': self.fbc,
            'U&E': self.uande,
            'Urea & Electrolytes': self.uande,
            'HbA1c': self.hba1c,
            'Lipids': self.lipids,
            'Lipid Profile': self.lipids,
            'ECG': self.ecg,
            'Echo': self.echo,
            'Echocardiogram': self.echo,
            'CT-Head': self.ct_head,
            'CT Head': self.ct_head,
            'Carotid-USS': self.carotid_doppler,
            'Carotid Doppler': self.carotid_doppler,
        }

        for key, gen in generators.items():
            if key in report_type:
                return gen(patient, conditions, abnormal)

        if event['type'] == 'gp':
            return self.gp_consultation(patient, conditions, event['title'])
        elif event['type'] == 'hospital':
            return self.ae_attendance(patient, conditions, event['title'], event.get('detail', 'Discharged'))
        elif event['type'] == 'diagnosis':
            return f"""CLINICAL CODING ENTRY

Diagnosis: {event['detail']}
Date: {event['date']}
SNOMED-CT: {random.randint(100000, 999999)}
ICD-10: {random.choice(['I', 'E', 'J', 'K'])}{random.randint(10, 99)}.{random.randint(0, 9)}

Clinical notes: New diagnosis of {event['detail'].lower()} confirmed following clinical assessment and appropriate investigations.
Management plan initiated as per NICE guidance.

Recorded by: {random.choice(self.doctors)}
"""
        return f"Report content for: {event['title']}"


class PatientGenerator:
    def __init__(self, seed=42):
        random.seed(seed)
        self.now = datetime(2026, 9, 12)
        self.reports = ReportGenerator()

    def generate(self, pid):
        age = self._age()
        sex = random.choice(["M", "F"])
        band = get_age_band(age)
        smoking = random.random() < SMOKING.get(band, 0.1)

        conditions = [c for c in PREVALENCE if random.random() < PREVALENCE[c].get(band, 0)]
        conditions = self._add_comorbidities(conditions, age)

        has_chronic = len(conditions) > 0
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

        self._generate_timeline(patient, conditions, has_chronic, age)

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

        for cond in conditions:
            years_ago = random.uniform(0.5, min(12, age - 20))
            date = self.now - timedelta(days=int(years_ago * 365))
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

        for year in range(5):
            year_start = self.now - timedelta(days=(5 - year) * 365)

            n_visits = random.randint(4, 10) if has_chronic else random.randint(1, 3)
            for _ in range(n_visits):
                date = year_start + timedelta(days=random.randint(0, 364))
                if date < self.now:
                    reasons = ["Routine review", "Medication review", "BP check", "Annual review"]
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

            n_reports = random.randint(2, 4) if has_chronic else random.randint(0, 1)
            for _ in range(n_reports):
                date = year_start + timedelta(days=random.randint(0, 364))
                if date < self.now:
                    rtype = random.choice(report_types)
                    abnormal = random.random() < 0.25
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

        if random.random() < (0.3 if has_chronic else 0.05):
            for _ in range(random.randint(1, 2)):
                date = self.now - timedelta(days=random.randint(30, 1500))
                complaints = ["Chest pain", "Breathlessness", "Collapse", "Palpitations", "Dizzy spell"]
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
            if (i+1) % 500 == 0: print(f"  {i+1}/{n}")
        return patients


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-n", type=int, default=5000)
    parser.add_argument("-o", default="data/patients_with_reports.json")
    args = parser.parse_args()

    print(f"Generating {args.n} patients with full report text...")
    gen = PatientGenerator()
    patients = gen.generate_cohort(args.n)

    strokes = sum(p["will_stroke"] for p in patients)
    avg_events = sum(len(p["events"]) for p in patients) / len(patients)

    print(f"\nStrokes: {strokes} ({100*strokes/len(patients):.1f}%)")
    print(f"Avg events/patient: {avg_events:.1f}")

    with open(args.o, "w") as f:
        json.dump(patients, f)
    print(f"Saved: {args.o}")
