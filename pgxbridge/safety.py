"""Contraindication checks against this patient's own record.

A rule set knows that a CYP2C19 poor metaboliser should not stay on clopidogrel.
It does not know that this particular patient is asthmatic and on a NOAC. These
checks run on the drafted alternative before anything reaches a GP, and a
blocking failure means the proposal is escalated for a human decision rather
than offered as a one-click switch.
"""
from __future__ import annotations

import re
from typing import Any

from .models import SafetyCheck

# Regimen-level cautions. Keyed by the regimen id used in the rule sets.
REGIMEN_CAUTIONS: dict[str, dict[str, Any]] = {
    "aspirin_dipyridamole": {
        "allergy_terms": ["aspirin", "salicylate", "nsaid", "dipyridamole"],
        "problem_terms": [
            "active peptic ulcer", "peptic ulcer", "gastrointestinal bleed", "gi bleed",
            "haemophilia", "hemophilia", "severe asthma", "aspirin-sensitive asthma",
        ],
        "problem_cautions": ["asthma", "hypotension", "heart failure"],
        "interaction_terms": ["warfarin", "apixaban", "rivaroxaban", "edoxaban", "dabigatran"],
    },
    "ticagrelor_aspirin": {
        "allergy_terms": ["aspirin", "salicylate", "ticagrelor"],
        "problem_terms": [
            "intracranial haemorrhage", "intracranial hemorrhage", "active bleeding",
            "severe hepatic impairment", "active peptic ulcer", "gastrointestinal bleed",
        ],
        "problem_cautions": ["asthma", "copd", "bradycardia", "gout", "ckd"],
        "interaction_terms": [
            "warfarin", "apixaban", "rivaroxaban", "edoxaban", "dabigatran",
            "clarithromycin", "ketoconazole", "ritonavir", "carbamazepine", "rifampicin",
            "simvastatin 80", "digoxin",
        ],
    },
    "paracetamol_nsaid": {
        "allergy_terms": ["paracetamol", "acetaminophen"],
        "problem_terms": ["severe hepatic impairment", "liver failure"],
        "problem_cautions": ["ckd", "chronic kidney disease"],
        "interaction_terms": [],
    },
}


def _norm(items: list[Any], *fields: str) -> list[str]:
    out = []
    for it in items or []:
        if isinstance(it, str):
            out.append(it.lower())
        elif isinstance(it, dict):
            for f in fields:
                v = it.get(f)
                if isinstance(v, str) and v.strip():
                    out.append(v.lower())
    return out


def check_regimen(regimen_id: str, alternative: dict[str, Any],
                  record: dict[str, Any]) -> list[SafetyCheck]:
    """Run every check for one proposed regimen against one patient record.

    `record` is the flattened patient picture built by the agent: allergies,
    problems and current medications.
    """
    caut = REGIMEN_CAUTIONS.get(regimen_id, {})
    allergies = _norm(record.get("allergies", []), "term", "title", "substance")
    problems = _norm(record.get("problems", []), "term", "title")
    current = [m for m in record.get("medications", [])
               if not isinstance(m, dict) or m.get("isCurrent", True)]
    meds = _norm(current, "term", "drug", "title")
    for script in record.get("prescriptions", []):
        if script.get("status") in ("cancelled", "rejected"):
            continue
        data = script.get("data") or {}
        order = data.get("medicationOrder") or {}
        meds.append(str(order.get("drug") or data.get("drug") or script.get("title") or "").lower())
    checks: list[SafetyCheck] = []

    # 1. Allergy to any component of the proposed regimen.
    hits = [a for a in allergies
            if any(t in a for t in caut.get("allergy_terms", []))]
    checks.append(SafetyCheck(
        check="allergy",
        passed=not hits,
        detail=("Recorded allergy to " + ", ".join(sorted(set(hits)))) if hits
               else "No recorded allergy to any component of the proposed regimen.",
        blocking=True,
    ))

    # 2. Absolute contraindications in the problem list.
    hits = [p for p in problems
            if any(t in p for t in caut.get("problem_terms", []))]
    checks.append(SafetyCheck(
        check="contraindication",
        passed=not hits,
        detail=("Contraindicated by recorded problem: " + ", ".join(sorted(set(hits)))) if hits
               else "No absolute contraindication in the problem list.",
        blocking=True,
    ))

    # 3. Interacting medicine already on repeat. Blocking: prescriber decides.
    hits = [m for m in meds
            if any(t in m for t in caut.get("interaction_terms", []))]
    checks.append(SafetyCheck(
        check="interaction",
        passed=not hits,
        detail=("Interacting medicine on the current list: " + ", ".join(sorted(set(hits)))) if hits
               else "No interacting medicine on the current list.",
        blocking=True,
    ))

    # 4. Cautions. Surfaced to the prescriber but do not block the offer.
    hits = [p for p in problems
            if any(t in p for t in caut.get("problem_cautions", []))]
    checks.append(SafetyCheck(
        check="caution",
        passed=not hits,
        detail=("Use with caution: " + ", ".join(sorted(set(hits)))) if hits
               else "No cautions flagged.",
        blocking=False,
    ))

    return checks


def blocking_failures(checks: list[SafetyCheck]) -> list[str]:
    return [f"{c.check}: {c.detail}" for c in checks if c.blocking and not c.passed]


def cautions(checks: list[SafetyCheck]) -> list[str]:
    return [c.detail for c in checks if not c.blocking and not c.passed]
