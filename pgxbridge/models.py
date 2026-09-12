"""Value types passed between the pipeline stages.

The pipeline is deliberately a chain of plain data objects so that every stage
can be tested in isolation and every decision can be replayed from an audit log.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class SourceRef:
    """Where a finding was read from. Kept on every downstream object."""
    site: str
    resource_id: str
    kind: str
    title: str
    section: str = ""
    observed_at: int | None = None

    def as_citation(self) -> str:
        bits = f"{self.site}:{self.resource_id} ({self.kind})"
        if self.section:
            bits += f" - {self.section}"
        return bits


@dataclass
class PgxFinding:
    """A raw pharmacogenomic assertion lifted out of free text.

    This is pre-normalisation: whatever the document actually said.
    """
    gene: str
    evidence: str                      # the verbatim span the call rests on
    source: SourceRef
    patient_id: str
    alleles: list[str] = field(default_factory=list)   # e.g. ["*2", "*2"]
    phenotype_phrase: str | None = None                # e.g. "poor metaboliser"
    carrier_allele: str | None = None                  # HLA-B style
    negated: bool = False
    confidence: float = 1.0
    extractor: str = "pattern"

    def fingerprint(self) -> str:
        raw = f"{self.patient_id}|{self.gene}|{sorted(self.alleles)}|{self.phenotype_phrase}|{self.carrier_allele}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Phenotype:
    """A normalised, SNOMED-coded metabolizer phenotype. The durable artefact."""
    gene: str
    phenotype: str                     # registry key, e.g. "poor_metaboliser"
    display: str
    snomed_code: str
    snomed_display: str
    diplotype: str | None = None       # e.g. "*2/*2"
    activity_score: float | None = None
    derivation: str = ""               # how we got here, for the audit trail
    confidence: float = 1.0
    sources: list[SourceRef] = field(default_factory=list)
    patient_id: str = ""

    def is_actionable(self, registry_gene: dict[str, Any]) -> bool:
        return self.phenotype in registry_gene.get("loss_of_function_phenotypes", [])


@dataclass
class SafetyCheck:
    """One contraindication test applied to a proposed regimen."""
    check: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass
class Proposal:
    """A drafted prescribing change awaiting a named clinician's authorisation."""
    patient_id: str
    patient_name: str
    gene: str
    phenotype: Phenotype
    rule_id: str
    rule_version: str
    severity: str
    action: str                        # switch | dose_reduce | hold | contraindicated | review | no_change
    summary: str
    current_drug: str | None = None
    current_resource_id: str | None = None
    current_version: int = 1
    current_owner: str = "pharmacy"
    proposed_regimen: dict[str, Any] | None = None
    safety: list[SafetyCheck] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    status: str = "drafted"            # also routed, rejected, applied, escalated, incomplete, needs_review
    task_id: str | None = None
    routed_at: int | None = None
    authorised_by: str | None = None

    @property
    def safe_to_offer(self) -> bool:
        return not self.blocked_by

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
