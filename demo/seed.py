"""Seed the simulated estate with the scenario the agent is meant to solve.

Every case below puts a pharmacogenomic result somewhere it would really land -
a results block, a paragraph of discharge narrative, a line in 'GP actions' -
and then puts the patient on the drug the result invalidates. Four cases are
negative controls that must NOT fire.

Synthetic data only. Run once per world.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pgxbridge.sim import SimClient, SimError

# (patient_id, label, discharge sections, medicine to put them on)
CASES = [
    # --- 1. the canonical case: clean structured lab result -------------
    ("SIM-000011", "CYP2C19 *2/*2 PM on clopidogrel", {
        "reason": "Admitted with right-sided weakness and expressive dysphasia. Diagnosis: left MCA territory ischaemic stroke.",
        "course": "Thrombolysed within window with good recovery. Swallow screen passed. Mobilised with physiotherapy and discharged home day 4.",
        "diagnoses": "1. Acute ischaemic stroke (left MCA). 2. Hypertension.",
        "medicationChanges": "STARTED: Clopidogrel 75mg once daily for secondary prevention. Atorvastatin 80mg nocte. CONTINUED: Ramipril 5mg od.",
        "results": ("CT head: established left MCA infarct. Carotid dopplers: no significant stenosis.\n"
                    "PHARMACOGENOMICS (Regional Genomics Laboratory Hub): CYP2C19 genotype *2/*2. "
                    "Predicted phenotype: poor metaboliser. Reduced conversion of clopidogrel to its "
                    "active metabolite; diminished antiplatelet effect expected."),
        "followUp": "Stroke clinic in 6 weeks. BP target below 130/80.",
        "gpActions": "Please continue secondary prevention and review BP at 2 weeks.",
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after ischaemic stroke"}),

    # --- 2. result buried in narrative prose, no lab block --------------
    ("SIM-000012", "CYP2C19 *1/*2 IM, narrative only", {
        "reason": "Transient right arm weakness and slurred speech lasting 40 minutes. Diagnosis: TIA.",
        "course": ("Seen in the TIA clinic within 24 hours. Symptoms had fully resolved. Imaging "
                   "was unremarkable. A pharmacogenomic panel was sent at first contact and the "
                   "result returned after she had gone home: she carries one loss-of-function "
                   "CYP2C19 allele (*1/*2), an intermediate metaboliser. This was not available "
                   "when the discharge prescription was written."),
        "diagnoses": "Transient ischaemic attack.",
        "medicationChanges": "STARTED: Clopidogrel 75mg once daily.",
        "results": "MRI head: no acute infarct. ECG: sinus rhythm.",
        "followUp": "Discharged to GP care.",
        "gpActions": "Continue antiplatelet. Address vascular risk factors.",
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after TIA"}),

    # --- 3. variant-level reporting by rsID, in 'GP actions' ------------
    ("SIM-000013", "CYP2C19 rs4244285 homozygous, in gpActions", {
        "reason": "Posterior circulation TIA.",
        "course": "Observed overnight. No further episodes. Discharged with antiplatelet therapy.",
        "diagnoses": "Transient ischaemic attack, posterior circulation.",
        "medicationChanges": "STARTED: Clopidogrel 75mg od.",
        "results": "CT angiogram: mild vertebral irregularity, no occlusion.",
        "followUp": "TIA clinic review at 4 weeks.",
        "gpActions": ("Please note the genomics report attached separately: rs4244285 homozygous "
                      "for the variant allele. The laboratory comments that clopidogrel activation "
                      "will be impaired. Please action in primary care."),
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after TIA"}),

    # --- 4. gene-agnostic proof: DPYD before capecitabine ---------------
    ("SIM-000014", "DPYD partial deficiency on capecitabine", {
        "reason": "Elective admission for colorectal cancer staging and treatment planning.",
        "course": "Staging completed. Adjuvant chemotherapy planned with oncology.",
        "diagnoses": "Stage III colorectal adenocarcinoma.",
        "medicationChanges": "STARTED: Capecitabine as per oncology protocol.",
        "results": ("DPYD genotyping performed prior to fluoropyrimidine: c.2846A>T heterozygous. "
                    "Partial DPD deficiency. Dose reduction advised per national guidance."),
        "followUp": "Oncology day unit, cycle 1.",
        "gpActions": "For information. Oncology retains prescribing responsibility.",
    }, {"drug": "Capecitabine 500mg tablets", "dose": "1250", "unit": "mg", "route": "oral",
        "frequency": "twice daily", "duration": "14 days", "quantity": 112,
        "indication": "Adjuvant chemotherapy for colorectal cancer"}),

    # --- 5. gene-agnostic proof: HLA-B*57:01 before abacavir ------------
    ("SIM-000015", "HLA-B*57:01 positive on abacavir", {
        "reason": "Routine HIV clinic review with treatment switch planning.",
        "course": "Virological suppression maintained. Regimen simplification discussed.",
        "diagnoses": "HIV-1 infection, virologically suppressed.",
        "medicationChanges": "PLANNED: switch to an abacavir-containing regimen.",
        "results": "HLA-B*57:01 screening: allele detected. Positive.",
        "followUp": "HIV clinic in 3 months.",
        "gpActions": "Shared care. Specialist team leads antiretroviral prescribing.",
    }, {"drug": "Abacavir 300mg tablets", "dose": "300", "unit": "mg", "route": "oral",
        "frequency": "twice daily", "duration": "28 days", "quantity": 56,
        "indication": "Antiretroviral therapy"}),

    # --- 6. gene-agnostic proof: TPMT before azathioprine ---------------
    ("SIM-000016", "TPMT poor metaboliser on azathioprine", {
        "reason": "Inflammatory bowel disease flare requiring steroid-sparing agent.",
        "course": "Settled on intravenous steroids. Maintenance therapy planned.",
        "diagnoses": "Crohn's disease.",
        "medicationChanges": "STARTED: Azathioprine 100mg once daily.",
        "results": "TPMT genotype *3A/*3C. Absent TPMT activity - poor metaboliser.",
        "followUp": "Gastroenterology in 6 weeks with FBC.",
        "gpActions": "Please arrange weekly FBC for the first 8 weeks.",
    }, {"drug": "Azathioprine 50mg tablets", "dose": "100", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 56,
        "indication": "Maintenance therapy for Crohn's disease"}),

    # --- 7. NEGATIVE CONTROL: normal metaboliser, must not fire ---------
    ("SIM-000017", "CONTROL normal metaboliser", {
        "reason": "Minor stroke, fully resolved.",
        "course": "Uncomplicated admission, discharged day 2.",
        "diagnoses": "Minor ischaemic stroke.",
        "medicationChanges": "STARTED: Clopidogrel 75mg od.",
        "results": "CYP2C19 genotype *1/*1. Predicted phenotype: normal metaboliser. "
                   "Standard clopidogrel dosing appropriate.",
        "followUp": "Stroke clinic 6 weeks.",
        "gpActions": "Continue as prescribed.",
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after stroke"}),

    # --- 8. NEGATIVE CONTROL: result pending, must not fire -------------
    ("SIM-000018", "CONTROL result pending", {
        "reason": "TIA, resolved.",
        "course": "Discharged same day from the TIA clinic.",
        "diagnoses": "Transient ischaemic attack.",
        "medicationChanges": "STARTED: Clopidogrel 75mg od.",
        "results": "CYP2C19 genotype requested; sample sent to the genomics hub. Result pending.",
        "followUp": "TIA clinic 4 weeks.",
        "gpActions": "Await pharmacogenomic result.",
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after TIA"}),

    # --- 9. NEGATIVE CONTROL: family history, must not fire -------------
    ("SIM-000019", "CONTROL family history only", {
        "reason": "TIA, resolved.",
        "course": "Observed and discharged.",
        "diagnoses": "Transient ischaemic attack.",
        "medicationChanges": "STARTED: Clopidogrel 75mg od.",
        "results": "No genomic testing performed this admission.",
        "followUp": "TIA clinic 4 weeks.",
        "gpActions": "Of note, her mother is a known CYP2C19 poor metaboliser. "
                     "Consider testing this patient if clinically indicated.",
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after TIA"}),

    # --- 10. NEGATIVE CONTROL: HLA-B*57:01 negative, must not fire ------
    ("SIM-000020", "CONTROL HLA-B*57:01 negative", {
        "reason": "HIV clinic review.",
        "course": "Stable. Regimen switch considered.",
        "diagnoses": "HIV-1 infection.",
        "medicationChanges": "PLANNED: abacavir-containing regimen.",
        "results": "HLA-B*57:01 screening: allele not detected. Negative.",
        "followUp": "HIV clinic 3 months.",
        "gpActions": "Shared care.",
    }, {"drug": "Abacavir 300mg tablets", "dose": "300", "unit": "mg", "route": "oral",
        "frequency": "twice daily", "duration": "28 days", "quantity": 56,
        "indication": "Antiretroviral therapy"}),
    # --- 11. SAFETY: indication rules out first-line, falls to ticagrelor --
    ("SIM-000021", "CYP2C19 *2/*3 PM after PCI", {
        "reason": "NSTEMI treated with percutaneous coronary intervention and drug-eluting stent.",
        "course": "Uncomplicated PCI to the LAD. Dual antiplatelet therapy commenced.",
        "diagnoses": "NSTEMI. Coronary artery disease.",
        "medicationChanges": "STARTED: Clopidogrel 75mg od plus aspirin 75mg od.",
        "results": "CYP2C19 *2/*3 - poor metaboliser. Clopidogrel not recommended after stenting.",
        "followUp": "Cardiology 6 weeks.",
        "gpActions": "Continue DAPT for 12 months.",
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Dual antiplatelet therapy following PCI for ACS"}),

    # --- 12. SAFETY: allergy blocks every alternative -> escalate ---------
    ("SIM-000022", "CYP2C19 *2/*2 PM with aspirin allergy", {
        "reason": "Left hemisphere TIA.",
        "course": "Resolved within the hour. Discharged on antiplatelet therapy.",
        "diagnoses": "Transient ischaemic attack.",
        "medicationChanges": "STARTED: Clopidogrel 75mg od.",
        "results": "CYP2C19 *2/*2 poor metaboliser.",
        "followUp": "TIA clinic 4 weeks.",
        "gpActions": "Note documented aspirin sensitivity.",
    }, {"drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after TIA"}),
]

# Allergies that must be on the record before the agent runs.
ALLERGIES = [("SIM-000022", "Aspirin", "Urticaria and bronchospasm")]


def seed(client: SimClient) -> list[dict]:
    out = []
    for pid, label, sections, med in CASES:
        row = {"patient": pid, "label": label}
        try:
            doc = client.save_discharge_summary(
                pid, f"Northbank General - discharge summary ({label.split()[0]})", sections
            )
            client.process_document("hospital", doc["id"], doc["version"], "send",
                                    "Sent to the registered practice.")
            row["document"] = doc["id"]
        except SimError as exc:
            row["document_error"] = str(exc)
        try:
            rx = client.draft_prescription(pid, med["drug"], med)
            row["prescription"] = rx["id"]
        except SimError as exc:
            row["prescription_error"] = str(exc)
        out.append(row)
        print(f"  {pid}  {label:46} doc={row.get('document','ERR')} rx={row.get('prescription','ERR')}")
    for pid, substance, reaction in ALLERGIES:
        try:
            client.action("gp", {
                "type": "save_allergy", "patientId": pid, "title": substance,
                "allergyStatus": "active", "reaction": reaction,
            })
            print(f"  {pid}  allergy recorded: {substance} ({reaction})")
        except SimError as exc:
            print(f"  {pid}  allergy FAILED: {exc}")
    return out


if __name__ == "__main__":
    import os
    c = SimClient(os.environ.get("NHS_SIM_KEY"))
    print(f"Seeding world {c.team()['world']}\n")
    seed(c)
    print("\nDone.")
