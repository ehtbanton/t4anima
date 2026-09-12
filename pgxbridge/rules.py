"""Deterministic rule engine.

No model runs here. Given (phenotype, drug, indication) this function returns
the same answer every time, and the answer carries the rule id and version that
produced it. That is what makes the output defensible in an incident review.
"""
from __future__ import annotations

from typing import Any

from .models import Phenotype
from . import registry


def evaluate(phenotype: Phenotype, drug_text: str,
             indication: str = "") -> list[dict[str, Any]]:
    """Every rule triggered by this phenotype for this drug."""
    hits = []
    for rs in registry.rules_for_drug(drug_text):
        if rs["gene"].upper() != phenotype.gene.upper():
            continue
        rec = rs["recommendations"].get(phenotype.phenotype)
        if not rec or rec.get("action") == "no_change":
            continue
        scope = rs.get("indication_scope") or []
        if scope and indication:
            if not any(s in indication.lower() for s in scope):
                continue
        hits.append({
            "rule_id": rs["rule_id"],
            "rule_version": rs["rule_version"],
            "gene": rs["gene"],
            "source": rs.get("source", ""),
            "severity": rec["severity"],
            "action": rec["action"],
            "summary": rec["summary"],
            "alternatives": rec.get("alternatives", []),
            "dose_modifier": rec.get("dose_modifier"),
            "escalate_to": rec.get("escalate_to"),
        })
    return hits


def choose_alternative(hit: dict[str, Any], indication: str,
                       excluded: set[str] | None = None) -> dict[str, Any] | None:
    """Highest-ranked alternative that fits the indication and is not excluded."""
    excluded = excluded or set()
    candidates = sorted(hit.get("alternatives", []), key=lambda a: a.get("rank", 99))
    for alt in candidates:
        if alt["regimen"] in excluded:
            continue
        filt = alt.get("indication_filter") or []
        if filt and indication:
            if not any(f in indication.lower() for f in filt):
                continue
        return alt
    return None


def severity_rank(severity: str) -> int:
    return {"critical": 3, "high": 2, "moderate": 1, "none": 0}.get(severity, 0)
