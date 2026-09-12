"""Normalisation: raw findings -> one SNOMED-coded metabolizer phenotype per gene.

This is the step that turns a sentence in a PDF into a structured fact with a
lifetime. Three gene models are supported and the registry says which applies:

  diplotype      count no-function / increased-function alleles (CYP2C19, TPMT)
  activity_score sum per-allele activity values and band it (DPYD, CYP2D6)
  carrier        presence or absence of a named risk allele (HLA-B)

A gene added to genes.json with one of these models needs no code change here.
"""
from __future__ import annotations

from collections import Counter

from .models import PgxFinding, Phenotype
from . import registry


class PhenotypeError(ValueError):
    pass


def normalise(findings: list[PgxFinding]) -> list[Phenotype]:
    """Collapse all findings for a patient into one phenotype per gene."""
    by_gene: dict[str, list[PgxFinding]] = {}
    for f in findings:
        by_gene.setdefault(f.gene.upper(), []).append(f)

    out: list[Phenotype] = []
    for gene, group in by_gene.items():
        gdef = registry.gene_def(gene)
        if not gdef:
            continue
        pheno = _resolve_gene(gene, gdef, group)
        if pheno:
            out.append(pheno)
    return out


def _resolve_gene(gene: str, gdef: dict, findings: list[PgxFinding]) -> Phenotype | None:
    model = gdef.get("model")
    # Prefer the highest-confidence finding that actually carries genotype data.
    ranked = sorted(findings, key=lambda f: (bool(f.alleles or f.carrier_allele), f.confidence),
                    reverse=True)

    if model == "carrier":
        return _resolve_carrier(gene, gdef, ranked)

    best = ranked[0]
    alleles = _canonical_alleles(gdef, best.alleles)

    if alleles:
        if model == "activity_score":
            pheno_key, score, how = _band_activity(gdef, alleles)
        else:
            pheno_key, score, how = _count_function(gdef, alleles)
        diplotype = "/".join(alleles)
        derivation = f"diplotype {diplotype} -> {how}"
        confidence = best.confidence
        # If the document also stated a phenotype in words and it disagrees with
        # the genotype call, keep the genotype but drop confidence and say so.
        stated = _stated_phenotype(gdef, findings)
        if stated and stated != pheno_key and stated != "__lof__":
            derivation += f"; document stated '{stated}' (genotype call retained)"
            confidence = min(confidence, 0.6)
    else:
        stated = _stated_phenotype(gdef, findings)
        if not stated:
            return None
        if stated == "__lof__":
            # "loss-of-function carrier" with no diplotype: the weakest actionable
            # call the registry allows, never the most severe one.
            lof = gdef.get("loss_of_function_phenotypes", [])
            stated = lof[-1] if lof else None
            if not stated:
                return None
            derivation = "narrative loss-of-function statement, no diplotype reported"
        else:
            derivation = f"phenotype stated in text as '{stated}'"
        pheno_key, score, diplotype = stated, None, None
        confidence = min(best.confidence, 0.8)

    spec = gdef["phenotypes"].get(pheno_key)
    if not spec:
        return None

    return Phenotype(
        gene=gene,
        phenotype=pheno_key,
        display=spec["display"],
        snomed_code=spec["snomed"]["code"],
        snomed_display=spec["snomed"]["display"],
        diplotype=diplotype,
        activity_score=score,
        derivation=derivation,
        confidence=confidence,
        sources=[f.source for f in findings],
        patient_id=findings[0].patient_id,
    )


def _resolve_carrier(gene: str, gdef: dict, findings: list[PgxFinding]) -> Phenotype | None:
    carriers = [f for f in findings if f.carrier_allele and not f.negated]
    negatives = [f for f in findings if f.carrier_allele and f.negated]
    if carriers:
        best = carriers[0]
        allele = best.carrier_allele
        key = next((k for k, v in gdef["phenotypes"].items() if v.get("allele") == allele), None)
        if not key:
            return None
        spec = gdef["phenotypes"][key]
        return Phenotype(
            gene=gene, phenotype=key, display=spec["display"],
            snomed_code=spec["snomed"]["code"], snomed_display=spec["snomed"]["display"],
            diplotype=allele, derivation=f"{allele} reported present",
            confidence=best.confidence,
            sources=[f.source for f in findings], patient_id=best.patient_id,
        )
    if negatives:
        best = negatives[0]
        spec = gdef["phenotypes"]["negative"]
        return Phenotype(
            gene=gene, phenotype="negative", display=spec["display"],
            snomed_code=spec["snomed"]["code"], snomed_display=spec["snomed"]["display"],
            diplotype=best.carrier_allele,
            derivation=f"{best.carrier_allele} reported not detected",
            confidence=best.confidence,
            sources=[f.source for f in findings], patient_id=best.patient_id,
        )
    return None


def _canonical_alleles(gdef: dict, alleles: list[str]) -> list[str]:
    """Map aliases onto registry allele names; drop anything unrecognised."""
    fn = gdef.get("allele_function", {})
    aliases = {k.lower(): v for k, v in gdef.get("variant_aliases", {}).items()}
    out = []
    for a in alleles:
        a = a.strip()
        if a in fn:
            out.append(a)
            continue
        key = a.lstrip("*").lower()
        if key in aliases and aliases[key] in fn:
            out.append(aliases[key])
        elif a.lower() in aliases and aliases[a.lower()] in fn:
            out.append(aliases[a.lower()])
        elif a in ("*1",):
            out.append("*1")
    return out if len(out) == 2 else []


def _count_function(gdef: dict, alleles: list[str]) -> tuple[str, None, str]:
    """Diplotype model: match the first phenotype whose rule the counts satisfy."""
    fn = gdef["allele_function"]
    counts = Counter(fn.get(a, "normal") for a in alleles)
    for key, spec in gdef["phenotypes"].items():
        rule = spec.get("rule")
        if not rule:
            continue
        if "none" in rule and counts.get("none", 0) != rule["none"]:
            continue
        if "increased" in rule and counts.get("increased", 0) != rule["increased"]:
            continue
        if "with" in rule:
            others = [fn.get(a, "normal") for a in alleles]
            # remove one no-function allele, the rest must be in the 'with' list
            others.remove("none")
            if not all(o in rule["with"] for o in others):
                continue
        return key, None, f"{dict(counts)} matches {key}"
    return "normal_metaboliser", None, "no rule matched; defaulted to normal"


def _band_activity(gdef: dict, alleles: list[str]) -> tuple[str, float, str]:
    """Activity-score model: sum per-allele values, band into a phenotype."""
    fn = gdef["allele_function"]
    vals = gdef.get("activity_values", {"normal": 1.0, "decreased": 0.5, "none": 0.0})
    score = sum(vals.get(fn.get(a, "normal"), 1.0) for a in alleles)
    for key, spec in gdef["phenotypes"].items():
        rng = spec.get("activity_range")
        if rng and rng[0] - 1e-9 <= score <= rng[1] + 1e-9:
            return key, score, f"activity score {score} in band {rng}"
    return "normal_metaboliser", score, f"activity score {score} outside all bands"


def _stated_phenotype(gdef: dict, findings: list[PgxFinding]) -> str | None:
    for f in sorted(findings, key=lambda x: x.confidence, reverse=True):
        if f.phenotype_phrase and not f.negated:
            return f.phenotype_phrase
    return None
