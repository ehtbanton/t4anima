"""Perception layer: pull pharmacogenomic assertions out of unstructured text.

Two extractors, same interface:

  PatternExtractor  deterministic, always runs, high precision. Diplotype strings
                    ("CYP2C19 *2/*2"), rsIDs, HLA carriage and the standard
                    metabolizer phrases are regular enough that a model is the
                    wrong tool - and a regex can be unit tested against a corpus.

  LlmExtractor      optional, for narrative that the patterns miss ("the
                    genotype result confirms she will not activate clopidogrel").
                    Returns the same PgxFinding objects and is always reconciled
                    against the pattern layer before anything is coded.

Nothing here writes. Extraction is pure: text in, findings out.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Protocol

from .models import PgxFinding, SourceRef
from . import registry

# --- lexical building blocks ------------------------------------------------

# Gene names as they actually appear in letters, including the sloppy variants.
GENE_PATTERNS = {
    "CYP2C19": r"CYP\s*-?\s*2\s*C\s*19",
    "CYP2D6": r"CYP\s*-?\s*2\s*D\s*6",
    "DPYD": r"DPYD|DPD\b|dihydropyrimidine\s+dehydrogenase",
    "TPMT": r"TPMT|thiopurine\s+methyltransferase",
    "HLA-B": r"HLA\s*-?\s*B",
}

# *2/*2, *1/*17, *2/*2 (no space), and "genotype 2/2"
DIPLOTYPE_RE = re.compile(r"\*\s*(\w{1,4})\s*/\s*\*?\s*(\w{1,4})")
# HLA alleles: *57:01, *15:02
HLA_ALLELE_RE = re.compile(r"\*\s*(\d{2}\s*:\s*\d{2})")
RSID_RE = re.compile(r"\b(rs\d{3,10})\b", re.I)
HGVS_RE = re.compile(r"\b(c\.\s*\d+[+\-]?\d*\s*[ACGT]\s*>\s*[ACGT])", re.I)

NEGATION_CUES = [
    "not detected", "no variant", "no variants", "negative", "wild type", "wild-type",
    "absent", "none detected", "not carried", "no loss-of-function", "no loss of function",
    "normal result", "no pathogenic",
]
POSITIVE_CUES = ["positive", "detected", "present", "carrier", "heterozygous", "homozygous"]

# A genotype belonging to someone else must never be coded onto this patient.
# Family-history sentences are common in genomics letters and are the single
# most dangerous false positive this extractor can make.
THIRD_PARTY_CUES = [
    "mother", "father", "sister", "brother", "sibling", "parent", "son", "daughter",
    "family history", "relative", "aunt", "uncle", "cousin", "her son", "his son",
    "maternal", "paternal", "proband's", "next of kin", "twin",
]

# Statements about a test that has not produced a result yet.
PENDING_CUES = [
    "pending", "awaited", "awaiting", "requested", "to be sent", "has been sent",
    "sample sent", "will be tested", "result to follow", "not yet available",
]

LOF_PHRASES = [
    "loss-of-function", "loss of function", "reduced function", "non-functional",
    "will not activate", "does not activate", "reduced activation", "impaired activation",
    "inadequate response", "will derive little benefit", "unlikely to benefit",
]

# Sentence-ish splitting that survives "*2/*2." and "c.1905+1G>A."
_SENT_SPLIT = re.compile(r"(?<=[.;!?])\s+(?=[A-Z(])")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def _window(text: str, start: int, end: int, pad: int = 140) -> str:
    return text[max(0, start - pad): min(len(text), end + pad)].replace("\n", " ").strip()


def _is_third_party(span: str) -> bool:
    low = span.lower()
    return any(cue in low for cue in THIRD_PARTY_CUES)


def _is_pending(span: str) -> bool:
    low = span.lower()
    return any(cue in low for cue in PENDING_CUES)


def _is_negated(span: str) -> bool:
    low = span.lower()
    neg = any(cue in low for cue in NEGATION_CUES)
    if not neg:
        return False
    # Remove the negation phrases before looking for positive cues, otherwise
    # "not detected" reads as positive because it contains "detected".
    residual = low
    for cue in NEGATION_CUES:
        residual = residual.replace(cue, " ")
    pos = any(cue in residual for cue in POSITIVE_CUES)
    # "HLA-B*57:01 negative" negates; "*57:01 detected" does not.
    return not pos


def _variant_index() -> dict[str, str]:
    """variant identifier -> gene, built from the registry."""
    idx: dict[str, str] = {}
    for gene in registry.known_genes():
        gdef = registry.gene_def(gene) or {}
        for alias in gdef.get("variant_aliases", {}):
            idx[re.sub(r"\s+", "", alias).lower()] = gene
        for allele in gdef.get("allele_function", {}):
            if allele.startswith("c.") or allele.lower().startswith("rs"):
                idx[re.sub(r"\s+", "", allele).lower()] = gene
    return idx


class Extractor(Protocol):
    name: str

    def extract(self, text: str, source: SourceRef, patient_id: str) -> list[PgxFinding]:
        ...


class PatternExtractor:
    """Deterministic extraction. High precision, auditable, unit-testable."""

    name = "pattern"

    def __init__(self, genes: Iterable[str] | None = None):
        self.genes = list(genes) if genes else registry.known_genes()
        self._compiled = {
            g: re.compile(GENE_PATTERNS[g], re.I) for g in self.genes if g in GENE_PATTERNS
        }
        self._variants = {k: v for k, v in _variant_index().items() if v in self.genes}

    def extract(self, text: str, source: SourceRef, patient_id: str) -> list[PgxFinding]:
        if not text:
            return []
        findings: list[PgxFinding] = []
        for gene, rx in self._compiled.items():
            gdef = registry.gene_def(gene)
            if not gdef:
                continue
            for m in rx.finditer(text):
                span = _window(text, m.start(), m.end())
                # Attribution and tense guards run before any call is made.
                if _is_third_party(span) or _is_pending(span):
                    continue
                finding = self._call(gene, gdef, span, source, patient_id)
                if finding:
                    findings.append(finding)

        # Second pass: variants quoted without their gene name. A report saying
        # only "rs4244285 homozygous" is still a CYP2C19 result.
        for m in list(RSID_RE.finditer(text)) + list(HGVS_RE.finditer(text)):
            key = re.sub(r"\s+", "", m.group(1)).lower()
            gene = self._variants.get(key)
            if not gene:
                continue
            span = _window(text, m.start(), m.end())
            if _is_third_party(span) or _is_pending(span):
                continue
            gdef = registry.gene_def(gene)
            finding = self._call(gene, gdef, span, source, patient_id)
            if finding:
                findings.append(finding)
        return _dedupe(findings)

    def _call(self, gene: str, gdef: dict, span: str, source: SourceRef,
              patient_id: str) -> PgxFinding | None:
        model = gdef.get("model")
        aliases = {k.lower(): v for k, v in gdef.get("variant_aliases", {}).items()}

        if model == "carrier":
            allele_m = HLA_ALLELE_RE.search(span)
            allele = None
            if allele_m:
                allele = "*" + re.sub(r"\s+", "", allele_m.group(1))
            else:
                for rs in RSID_RE.findall(span):
                    if rs.lower() in aliases:
                        allele = aliases[rs.lower()]
                        break
            if not allele or allele not in gdef.get("carrier_alleles", []):
                return None
            return PgxFinding(
                gene=gene, evidence=span, source=source, patient_id=patient_id,
                carrier_allele=allele, negated=_is_negated(span),
                confidence=0.98, extractor=self.name,
            )

        # diplotype / activity-score genes
        alleles: list[str] = []
        dm = DIPLOTYPE_RE.search(span)
        if dm:
            alleles = [f"*{dm.group(1).strip()}", f"*{dm.group(2).strip()}"]
        else:
            # variant-level reporting: rsIDs or HGVS, map through aliases
            found = []
            fn_by_lower = {k.lower(): k for k in gdef.get("allele_function", {})}
            for token in RSID_RE.findall(span) + HGVS_RE.findall(span):
                key = re.sub(r"\s+", "", token).lower()
                if key in aliases:
                    found.append(aliases[key])
                elif key in fn_by_lower:
                    # already a registry allele name (e.g. DPYD "c.1905+1G>A")
                    found.append(fn_by_lower[key])
            if found:
                # a reported variant without a stated second allele is read as
                # heterozygous unless the text says homozygous
                if "homozygous" in span.lower():
                    alleles = [found[0], found[0]]
                else:
                    alleles = [found[0], "*1"]

        phrase = None
        low = span.lower()
        for p, pheno_key in sorted(gdef.get("phrase_phenotypes", {}).items(),
                                   key=lambda kv: -len(kv[0])):
            if p in low:
                phrase = pheno_key
                break
        if phrase is None and any(lp in low for lp in LOF_PHRASES):
            # an explicit loss-of-function statement with no phenotype word
            phrase = "__lof__"

        if not alleles and not phrase:
            return None

        conf = 0.99 if alleles and phrase else (0.95 if alleles else 0.85)
        return PgxFinding(
            gene=gene, evidence=span, source=source, patient_id=patient_id,
            alleles=alleles, phenotype_phrase=phrase,
            negated=_is_negated(span) and not alleles,
            confidence=conf, extractor=self.name,
        )


class LlmExtractor:
    """Narrative extraction via a model. Optional; pattern layer is the floor.

    `complete` is any callable taking a prompt string and returning the model's
    text. Kept injectable so the pipeline runs offline in tests and CI, and so
    the provider is a deployment choice rather than a code dependency.
    """

    name = "llm"

    PROMPT = """You are reading one section of an NHS clinical document. Extract ONLY
pharmacogenomic findings that are explicitly asserted about this patient.

Genes of interest: {genes}

Return a JSON array. Each element:
  {{"gene": str, "alleles": [str] or [], "phenotype_phrase": str or null,
    "carrier_allele": str or null, "negated": bool, "evidence": str}}

"evidence" must be a verbatim quote from the text. Do not infer a genotype from a
drug choice, a family history, or a plan to test. If the document only requests a
test or reports it as pending, return []. Return [] if nothing is asserted.

TEXT:
{text}

JSON:"""

    def __init__(self, complete, genes: Iterable[str] | None = None, min_confidence: float = 0.6):
        self.complete = complete
        self.genes = list(genes) if genes else registry.known_genes()
        self.min_confidence = min_confidence

    def extract(self, text: str, source: SourceRef, patient_id: str) -> list[PgxFinding]:
        if not text or not text.strip():
            return []
        try:
            raw = self.complete(self.PROMPT.format(genes=", ".join(self.genes), text=text[:6000]))
            items = _loads_array(raw)
        except Exception:
            # The model failing must never take the pipeline down; the pattern
            # layer still ran and its findings stand on their own.
            return []
        out = []
        for it in items:
            gene = str(it.get("gene", "")).upper()
            if gene not in self.genes:
                continue
            evidence = str(it.get("evidence", ""))[:600]
            if evidence and evidence[:40].lower() not in text.lower():
                # quote does not appear in the source: drop it rather than trust it
                continue
            out.append(PgxFinding(
                gene=gene,
                evidence=evidence or text[:300],
                source=source,
                patient_id=patient_id,
                alleles=[str(a) for a in (it.get("alleles") or [])],
                phenotype_phrase=it.get("phenotype_phrase"),
                carrier_allele=it.get("carrier_allele"),
                negated=bool(it.get("negated")),
                confidence=0.75,
                extractor=self.name,
            ))
        return out


def _loads_array(raw: str) -> list[dict]:
    raw = (raw or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1:
        return []
    parsed = json.loads(raw[start:end + 1])
    return parsed if isinstance(parsed, list) else []


def _dedupe(findings: list[PgxFinding]) -> list[PgxFinding]:
    seen: dict[str, PgxFinding] = {}
    for f in findings:
        key = f.fingerprint()
        if key not in seen or f.confidence > seen[key].confidence:
            seen[key] = f
    return list(seen.values())


def reconcile(pattern_findings: list[PgxFinding],
              llm_findings: list[PgxFinding]) -> list[PgxFinding]:
    """Pattern layer wins any disagreement on the same gene.

    The model is allowed to *add* a gene the patterns did not see; it is never
    allowed to overwrite a diplotype the patterns read directly.
    """
    out = list(pattern_findings)
    covered = {f.gene for f in pattern_findings if f.alleles or f.carrier_allele}
    for f in llm_findings:
        if f.gene in covered:
            continue
        out.append(f)
    return _dedupe(out)


# --- document traversal -----------------------------------------------------

TEXT_FIELDS = ("text", "body", "note", "comment", "content", "narrative",
               "report", "result", "conclusion", "impression", "summary")


def iter_text_blocks(resource: dict) -> list[tuple[str, str]]:
    """Every free-text block in a sim resource, as (section_label, text).

    Handles the shapes the estate actually uses: discharge summaries with named
    sections, notes with a sections array, and flat text fields.
    """
    blocks: list[tuple[str, str]] = []
    data = resource.get("data") or {}

    if resource.get("title"):
        blocks.append(("title", str(resource["title"])))

    sections = data.get("sections")
    if isinstance(sections, dict):
        for name, val in sections.items():
            if isinstance(val, str) and val.strip():
                blocks.append((name, val))
    elif isinstance(sections, list):
        for sec in sections:
            if isinstance(sec, dict):
                label = sec.get("heading") or sec.get("id") or "section"
                val = sec.get("text")
                if isinstance(val, str) and val.strip():
                    blocks.append((str(label), val))

    for field in TEXT_FIELDS:
        val = data.get(field)
        if isinstance(val, str) and val.strip():
            blocks.append((field, val))

    for key in ("entries", "attachments", "results", "observations"):
        val = data.get(key)
        if isinstance(val, list):
            for i, entry in enumerate(val):
                if isinstance(entry, dict):
                    for field in TEXT_FIELDS:
                        sub = entry.get(field)
                        if isinstance(sub, str) and sub.strip():
                            blocks.append((f"{key}[{i}].{field}", sub))
                elif isinstance(entry, str) and entry.strip():
                    blocks.append((f"{key}[{i}]", entry))
    return blocks


def scan_resource(resource: dict, extractors: list[Extractor],
                  site: str) -> list[PgxFinding]:
    """Run every extractor over every text block of one resource."""
    patient_id = resource.get("patientId") or ""
    if not patient_id:
        return []
    pattern_out: list[PgxFinding] = []
    llm_out: list[PgxFinding] = []
    for section, text in iter_text_blocks(resource):
        src = SourceRef(
            site=site,
            resource_id=resource.get("id", ""),
            kind=resource.get("kind", ""),
            title=str(resource.get("title", ""))[:160],
            section=section,
            observed_at=resource.get("createdAt"),
        )
        for ex in extractors:
            got = ex.extract(text, src, patient_id)
            (llm_out if ex.name == "llm" else pattern_out).extend(got)
    return reconcile(pattern_out, llm_out)
