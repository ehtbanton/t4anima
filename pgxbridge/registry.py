"""Loads the versioned gene definitions and drug-gene rule sets from disk.

Both are data, not code, and both are version-stamped. A clinical change is a
pull request against a JSON file with a bumped rule_version - reviewable by a
pharmacist who does not read Python.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

REGISTRY_DIR = Path(__file__).parent / "registry"


@lru_cache(maxsize=1)
def genes() -> dict[str, Any]:
    return json.loads((REGISTRY_DIR / "genes.json").read_text())


@lru_cache(maxsize=1)
def rule_sets() -> list[dict[str, Any]]:
    out = []
    for path in sorted((REGISTRY_DIR / "rules").glob("*.json")):
        out.append(json.loads(path.read_text()))
    return out


def gene_def(gene: str) -> dict[str, Any] | None:
    return genes()["genes"].get(gene.upper())


def known_genes() -> list[str]:
    return list(genes()["genes"].keys())


def rules_for_drug(drug_text: str) -> list[dict[str, Any]]:
    """Every rule set whose drug list matches this prescription free text."""
    low = (drug_text or "").lower()
    hits = []
    for rs in rule_sets():
        for d in rs["drugs"]:
            if d in low:
                hits.append(rs)
                break
    return hits


def rules_for_gene(gene: str) -> list[dict[str, Any]]:
    return [rs for rs in rule_sets() if rs["gene"].upper() == gene.upper()]


def all_watched_drugs() -> set[str]:
    return {d for rs in rule_sets() for d in rs["drugs"]}


def registry_stamp() -> dict[str, str]:
    """Provenance block written onto every decision."""
    stamp = {"genes_version": genes()["registry_version"]}
    for rs in rule_sets():
        stamp[rs["rule_id"]] = rs["rule_version"]
    return stamp
