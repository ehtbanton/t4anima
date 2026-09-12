"""The agent: scan, code, hook, draft, route, apply, chase.

Division of responsibility, which is the whole argument of this design:

  the model      perception (reading letters) and orchestration (what to do next)
  the rule sets  every clinical decision, deterministically and version-stamped
  a named GP     anything that touches a prescription

The agent never issues or cancels a medicine on its own authority. It drafts,
it checks, it routes, and it chases. Authorisation is a separate, recorded act
by a named clinician, and the ledger stores who.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from .models import Phenotype, Proposal, SafetyCheck, SourceRef
from . import extract, registry, rules, safety
from .extract import PatternExtractor, scan_resource
from .phenotype import normalise
from .sim import SimClient, SimError

# Sites that carry secondary-care correspondence in the simulated estate.
SOURCE_SITES = ("hospital", "gp", "diagnostics", "community")

DOC_KINDS = {
    "discharge-summary", "document", "note", "hospital-note", "letter",
    "clinic-letter", "observation", "report", "diagnostic-report", "screening",
    "ehr-record", "encounter",
}

CHASE_AFTER_MS = 48 * 60 * 60 * 1000
LEDGER_DEFAULT = Path("pgx_ledger.json")


class Ledger:
    """Append-only local audit trail. Every decision replayable from disk."""

    def __init__(self, path: Path = LEDGER_DEFAULT):
        self.path = Path(path)
        self.data: dict[str, Any] = {"phenotypes": {}, "proposals": {}, "events": []}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text())
            except json.JSONDecodeError:
                pass
        self.data.setdefault("phenotypes", {})
        self.data.setdefault("proposals", {})
        self.data.setdefault("events", [])

    def event(self, kind: str, **fields) -> None:
        self.data["events"].append({"at": int(time.time() * 1000), "kind": kind, **fields})

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2, default=str))

    def phenotype_key(self, patient_id: str, gene: str) -> str:
        return f"{patient_id}:{gene}"


class PgxAgent:
    def __init__(self, client: SimClient, extractors: list[Any] | None = None,
                 ledger: Ledger | None = None, dry_run: bool = False):
        self.sim = client
        self.extractors = extractors or [PatternExtractor()]
        self.ledger = ledger or Ledger()
        self.dry_run = dry_run
        self.stamp = registry.registry_stamp()

    # -- stage 1: read the secondary care record -------------------------
    def scan(self, patient_ids: Iterable[str] | None = None,
             sites: Iterable[str] = SOURCE_SITES,
             limit_per_site: int = 600) -> dict[str, list[Phenotype]]:
        """Extract PGx findings from correspondence and normalise per patient.

        Two modes. Given a worklist, query each patient's record directly - the
        estate holds hundreds of thousands of resources and paging blind through
        them finds nothing. Given no worklist, sweep the document endpoints,
        which is what a nightly backfill over historic correspondence does.
        """
        findings_by_patient: dict[str, list] = {}

        if patient_ids:
            for pid in patient_ids:
                for site in sites:
                    try:
                        page = {"resources": list(self.sim.resources(site, patient=pid, limit=200))}
                    except SimError as exc:
                        self.ledger.event("scan_error", site=site, patient=pid, error=str(exc))
                        continue
                    for res in page.get("resources", []):
                        if res.get("patientId") != pid:
                            continue
                        if res.get("kind") not in DOC_KINDS:
                            continue
                        got = scan_resource(res, self.extractors, site)
                        if got:
                            findings_by_patient.setdefault(pid, []).extend(got)
        else:
            for site, fetch in (("hospital", self.sim.hospital_documents),
                                ("gp", self.sim.gp_documents)):
                try:
                    payload = fetch() or {}
                except SimError as exc:
                    self.ledger.event("scan_error", site=site, error=str(exc))
                    continue
                for res in (payload.get("resources") or [])[:limit_per_site]:
                    pid = res.get("patientId")
                    if not pid:
                        continue
                    got = scan_resource(res, self.extractors, site)
                    if got:
                        findings_by_patient.setdefault(pid, []).extend(got)

        out: dict[str, list[Phenotype]] = {}
        for pid, findings in findings_by_patient.items():
            phenos = normalise(findings)
            if phenos:
                out[pid] = phenos
                self.ledger.event("extracted", patient=pid,
                                  genes=[p.gene for p in phenos],
                                  phenotypes=[p.phenotype for p in phenos])
        return out

    # -- stage 2: the phenotype store ------------------------------------
    def code(self, phenotypes: dict[str, list[Phenotype]]) -> list[dict]:
        """Write each phenotype into primary care as a SNOMED-coded problem.

        This is the durable artefact. It outlives the document it came from and
        is what every later prescribing moment reads.
        """
        written = []
        for pid, phenos in phenotypes.items():
            already = {} if self.dry_run else self.coded_snomed(pid)
            for ph in phenos:
                key = self.ledger.phenotype_key(pid, ph.gene)
                if key in self.ledger.data["phenotypes"]:
                    continue  # already coded; a genotype does not change
                record = {
                    **asdict(ph),
                    "coded_at": self.sim.now() if not self.dry_run else None,
                    "registry": self.stamp,
                }
                if ph.snomed_code in already:
                    # Already on the problem list. Adopt it into the ledger so the
                    # prescribing hook can fire, and do not write a duplicate.
                    record["resource_id"] = already[ph.snomed_code]
                    record["adopted"] = True
                    self.ledger.data["phenotypes"][key] = record
                    self.ledger.event("adopted_existing", patient=pid, gene=ph.gene,
                                      snomed=ph.snomed_code)
                    continue
                if not self.dry_run:
                    try:
                        res = self.sim.save_problem(
                            patient_id=pid,
                            title=ph.display,
                            code=ph.snomed_code,
                            status="active",
                        )
                        record["resource_id"] = res.get("id")
                        cites = "; ".join(s.as_citation() for s in ph.sources[:4])
                        self.sim.save_consultation(
                            patient_id=pid,
                            title=f"Pharmacogenomic result filed - {ph.gene}",
                            text=(
                                f"{ph.display} (SNOMED {ph.snomed_code} "
                                f"{ph.snomed_display}).\n"
                                f"Derivation: {ph.derivation}\n"
                                f"Confidence: {ph.confidence:.2f}\n"
                                f"Extracted from: {cites}\n"
                                f"Registry: genes {self.stamp['genes_version']}\n"
                                "This result is valid for life and will be checked "
                                "automatically at every future prescribing moment."
                            ),
                        )
                    except SimError as exc:
                        if "already active" in str(exc):
                            # Raced with another writer. Not an error: the fact
                            # is in the record, which is all that matters.
                            record["adopted"] = True
                            self.ledger.data["phenotypes"][key] = record
                            self.ledger.event("adopted_existing", patient=pid,
                                              gene=ph.gene, snomed=ph.snomed_code)
                            continue
                        self.ledger.event("code_error", patient=pid, gene=ph.gene,
                                          error=str(exc))
                        continue
                self.ledger.data["phenotypes"][key] = record
                self.ledger.event("coded", patient=pid, gene=ph.gene,
                                  phenotype=ph.phenotype, snomed=ph.snomed_code)
                written.append(record)
        return written

    def coded_snomed(self, patient_id: str) -> dict[str, str]:
        """SNOMED codes already on this patient's problem list.

        The record is the source of truth for what has been coded, not the local
        ledger - another instance of the agent, or a human, may have filed the
        same result already.
        """
        found: dict[str, str] = {}
        try:
            for res in self.sim.resources("gp", patient=patient_id, limit=200, max_pages=6):
                if res.get("kind") != "problem":
                    continue
                code = (res.get("data") or {}).get("code")
                if code:
                    found[str(code)] = res.get("id", "")
        except SimError as exc:
            self.ledger.event("record_error", patient=patient_id, error=str(exc))
        return found

    def stored_phenotypes(self, patient_id: str) -> list[Phenotype]:
        """Read the phenotype store back. The dormant result waking up."""
        out = []
        for key, rec in self.ledger.data["phenotypes"].items():
            if not key.startswith(f"{patient_id}:"):
                continue
            srcs = [SourceRef(**s) if isinstance(s, dict) else s
                    for s in rec.get("sources", [])]
            out.append(Phenotype(
                gene=rec["gene"], phenotype=rec["phenotype"], display=rec["display"],
                snomed_code=rec["snomed_code"], snomed_display=rec["snomed_display"],
                diplotype=rec.get("diplotype"), activity_score=rec.get("activity_score"),
                derivation=rec.get("derivation", ""), confidence=rec.get("confidence", 1.0),
                sources=srcs, patient_id=patient_id,
            ))
        return out

    # -- patient picture --------------------------------------------------
    def patient_record(self, patient_id: str) -> dict[str, Any]:
        """Flatten what primary care holds: problems, allergies, medications."""
        rec: dict[str, Any] = {"problems": [], "allergies": [], "medications": [],
                               "prescriptions": []}
        try:
            for res in self.sim.resources("gp", patient=patient_id, limit=200, max_pages=6):
                kind, data = res.get("kind"), res.get("data") or {}
                if kind == "ehr-record":
                    rec["problems"].extend(data.get("problems") or [])
                    rec["allergies"].extend(data.get("allergies") or [])
                    rec["medications"].extend(data.get("medications") or [])
                elif kind == "problem":
                    rec["problems"].append({"term": res.get("title"), "code": data.get("code")})
                elif kind == "allergy":
                    rec["allergies"].append({"term": res.get("title")})
                elif kind == "prescription":
                    rec["prescriptions"].append(res)
        except SimError as exc:
            self.ledger.event("record_error", patient=patient_id, error=str(exc))
            raise  # Incomplete safety data must not be interpreted as a clean record.
        meta = self.sim.patient(patient_id)
        if meta:
            rec["name"] = meta.get("name", patient_id)
            for c in meta.get("conditions") or []:
                rec["problems"].append({"term": c})
        rec.setdefault("name", patient_id)
        return rec

    def current_medicines(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        """Everything the patient is actually on, from both med list and scripts."""
        out = []
        for m in record.get("medications", []):
            if isinstance(m, dict) and m.get("isCurrent", True):
                out.append({
                    "drug": m.get("term", ""),
                    "indication": m.get("indication", ""),
                    "type": m.get("prescriptionType", ""),
                    "origin": "repeat_list",
                    "raw": m,
                })
        for p in record.get("prescriptions", []):
            if p.get("status") in ("cancelled", "rejected"):
                continue
            data = p.get("data") or {}
            order = data.get("medicationOrder") or {}
            out.append({
                "drug": order.get("drug") or data.get("drug") or p.get("title", ""),
                "indication": order.get("indication") or data.get("text", ""),
                "type": "acute",
                "origin": "prescription",
                "resource_id": p.get("id"),
                "version": p.get("version", 1),
                "owner": p.get("owner", "pharmacy"),
                "raw": p,
            })
        return out

    # -- stage 3+4: the prescribing hook and the drafted switch ----------
    def hook(self, patient_id: str) -> list[Proposal]:
        """Fire the stored phenotype against everything this patient is on.

        This is the moment the whole pipeline exists for, and it runs the same
        way whether the genotype was filed yesterday or nine years ago.
        """
        phenos = self.stored_phenotypes(patient_id)
        if not phenos:
            return []
        record = self.patient_record(patient_id)
        meds = self.current_medicines(record)
        proposals: list[Proposal] = []

        for ph in phenos:
            for med in meds:
                drug = med["drug"]
                if not drug:
                    continue
                for hit in rules.evaluate(ph, drug, med.get("indication", "")):
                    proposals.append(
                        self._draft(ph, hit, med, record, patient_id)
                    )
        proposals.sort(key=lambda p: -rules.severity_rank(p.severity))
        return proposals

    def _draft(self, ph: Phenotype, hit: dict, med: dict,
               record: dict, patient_id: str) -> Proposal:
        prop = Proposal(
            patient_id=patient_id,
            patient_name=record.get("name", patient_id),
            gene=ph.gene,
            phenotype=ph,
            rule_id=hit["rule_id"],
            rule_version=hit["rule_version"],
            severity=hit["severity"],
            action=hit["action"],
            summary=hit["summary"],
            current_drug=med["drug"],
            current_resource_id=med.get("resource_id"),
            current_version=med.get("version", 1),
        )
        prop.current_owner = med.get("owner", "pharmacy")

        # A dose reduction keeps the same drug, so there is no alternative to
        # choose. Compute the recommended dose and hand it to whoever owns the
        # prescribing - for a fluoropyrimidine or a thiopurine that is never the GP.
        if hit["action"] == "dose_reduce":
            modifier = hit.get("dose_modifier") or 0.5
            order = (med.get("raw") or {}).get("data", {}).get("medicationOrder") or {}
            current_dose = order.get("dose")
            reduced = None
            if current_dose:
                try:
                    reduced = f"{float(current_dose) * modifier:g}"
                except (TypeError, ValueError):
                    reduced = None
            prop.proposed_regimen = {
                "regimen": "dose_reduction",
                "label": (
                    f"Reduce {med['drug']} to {int(modifier * 100)}% of the standard dose"
                    + (f" ({reduced}{order.get('unit', '')} per dose)" if reduced else "")
                ),
                "components": [],
                "dose_modifier": modifier,
                "reduced_dose": reduced,
            }
            prop.status = "escalated"
            prop.blocked_by = [
                f"dose reduction must be made by {hit.get('escalate_to') or 'the prescribing team'}, "
                "not applied automatically in primary care"
            ]
            return prop

        # Actions that must not be auto-drafted as a switch go straight to a human.
        if hit["action"] in ("hold", "contraindicated", "review"):
            prop.status = "escalated"
            prop.blocked_by = [
                f"rule action '{hit['action']}' requires "
                f"{hit.get('escalate_to') or 'clinician'} review"
            ]
            return prop

        tried: set[str] = set()
        while True:
            alt = rules.choose_alternative(hit, med.get("indication", ""), excluded=tried)
            if not alt:
                prop.blocked_by = ["no alternative regimen passed the safety checks"]
                prop.status = "escalated"
                return prop
            checks = safety.check_regimen(alt["regimen"], alt, record)
            blocking = safety.blocking_failures(checks)
            if not blocking:
                prop.proposed_regimen = alt
                prop.safety = checks
                return prop
            tried.add(alt["regimen"])
            prop.safety = checks

    # -- stage 5: route as one decision ----------------------------------
    def _open_duplicate(self, prop: Proposal) -> str | None:
        """An open task already covering this patient, rule and medicine."""
        for task_id, raw in self.ledger.data["proposals"].items():
            if raw.get("status") not in ("routed", "drafted"):
                continue
            if (raw.get("patient_id") == prop.patient_id
                    and raw.get("rule_id") == prop.rule_id
                    and raw.get("current_drug") == prop.current_drug):
                return task_id
        return None

    def route(self, prop: Proposal) -> Proposal:
        """Put the drafted decision in front of the GP as a single choice.

        Idempotent: a finding that is already sitting in someone's inbox is not
        raised again. An agent that re-alerts on every cycle gets muted, and a
        muted agent is worth nothing.
        """
        dup = self._open_duplicate(prop)
        if dup:
            prop.task_id = dup
            prop.status = "routed"
            self.ledger.event("route_skipped_duplicate", patient=prop.patient_id, task=dup)
            return prop
        body = self.render(prop)
        title = (f"[{prop.severity.upper()}] {prop.gene} {prop.phenotype.display} "
                 f"- review {prop.current_drug}")
        if self.dry_run:
            prop.status = "routed"
            return prop
        try:
            task = self.sim.create_task("gp", prop.patient_id, title, body)
            prop.task_id = task.get("id")
            prop.routed_at = task.get("createdAt") or self.sim.now()
            prop.status = "routed"
            self.ledger.data["proposals"][prop.task_id] = prop.to_dict()
            self.ledger.event("routed", patient=prop.patient_id, task=prop.task_id,
                              rule=prop.rule_id, severity=prop.severity)
        except SimError as exc:
            self.ledger.event("route_error", patient=prop.patient_id, error=str(exc))
        return prop

    def render(self, prop: Proposal) -> str:
        """The one-click decision, as the GP sees it."""
        ph = prop.phenotype
        lines = [
            f"PHARMACOGENOMIC ALERT - {prop.severity.upper()}",
            "",
            f"Patient: {prop.patient_name} ({prop.patient_id})",
            f"Finding: {ph.display}"
            + (f"  [diplotype {ph.diplotype}]" if ph.diplotype else ""),
            f"SNOMED:  {ph.snomed_code} {ph.snomed_display}",
            f"Source:  " + "; ".join(s.as_citation() for s in ph.sources[:3]),
            "",
            f"Current medicine: {prop.current_drug}",
            "",
            "ASSESSMENT",
            f"  {prop.summary}",
            f"  Rule {prop.rule_id} v{prop.rule_version}",
        ]
        if prop.proposed_regimen:
            lines += ["", "PROPOSED CHANGE", f"  {prop.proposed_regimen['label']}"]
            for c in prop.proposed_regimen["components"]:
                lines.append(
                    f"    - {c['drug']}, {c['dose']}{c['unit']} {c['route']}, "
                    f"{c['frequency']}, {c['duration']} (qty {c['quantity']})"
                )
        if prop.safety:
            lines += ["", "SAFETY CHECKS AGAINST THIS PATIENT'S RECORD"]
            for c in prop.safety:
                mark = "PASS" if c.passed else ("BLOCK" if c.blocking else "CAUTION")
                lines.append(f"    [{mark}] {c.check}: {c.detail}")
        if prop.blocked_by:
            lines += ["", "NOT AUTO-DRAFTED"] + [f"    - {b}" for b in prop.blocked_by]
            lines += ["", "ACTION: clinician review required. No change has been made."]
        else:
            lines += [
                "",
                "TO AUTHORISE",
                "  Approving this task will, in one step:",
                "    1. create prescription drafts for the replacement regimen above",
                f"    2. cancel the {prop.current_drug} repeat",
                "    3. generate the simulated patient communication",
                "  Drafts still require the prescribing service's issue process.",
                "  Nothing changes until a named clinician approves.",
            ]
        lines += ["", "-- Generated by PGx Bridge. Synthetic simulation data only."]
        return "\n".join(lines)

    # -- stage 6: apply, only on a named clinician's authority ------------
    def authorise(self, task_id: str, clinician: str, approve: bool = True,
                  note: str = "") -> dict[str, Any]:
        """Apply a routed proposal. Requires a named clinician."""
        if not clinician or not clinician.strip():
            raise ValueError("authorise() requires a named clinician")
        raw = self.ledger.data["proposals"].get(task_id)
        if not raw:
            raise KeyError(f"no routed proposal for task {task_id}")
        if raw.get("status") in ("authorised", "rejected", "applied", "incomplete", "needs_review", "escalated"):
            return {"status": raw["status"], "note": "already actioned"}

        if not approve:
            raw["status"] = "rejected"
            raw["authorised_by"] = clinician
            self.ledger.event("rejected", task=task_id, clinician=clinician, note=note)
            self.ledger.save()
            return {"status": "rejected", "clinician": clinician}

        pid = raw["patient_id"]
        regimen = raw.get("proposed_regimen")
        if (raw.get("blocked_by") or raw.get("action") != "switch"
                or not regimen or not regimen.get("components")):
            raise ValueError("proposal requires clinician review and cannot be applied as a switch")

        record = self.patient_record(pid)
        checks = safety.check_regimen(regimen["regimen"], regimen, record)
        if safety.blocking_failures(checks):
            raise ValueError("Current record blocks this change: " + "; ".join(safety.blocking_failures(checks)))

        # Never issue the same switch twice. Another instance of the agent, an
        # earlier run, or a human may already have made this change, and a
        # duplicate antiplatelet prescription is a bleeding risk.
        clash = self._already_switched(pid, regimen)
        if clash:
            raw["status"] = "needs_review"
            raw["authorised_by"] = clinician
            self.ledger.event("apply_skipped_duplicate", task=task_id, patient=pid,
                              existing=clash)
            self.ledger.save()
            return {"status": "needs_review", "clinician": clinician,
                    "note": "One or more replacement components already exist; reconcile the complete regimen and original medicine before applying.",
                    "existing": clash}

        current = next((m for m in self.current_medicines(record)
                        if m.get("resource_id") == raw.get("current_resource_id")), None)
        if not raw.get("current_resource_id") or not current:
            raise ValueError("Original prescription is no longer active or needs manual repeat-list reconciliation")
        if current.get("version") != raw.get("current_version"):
            raise ValueError("Original prescription changed since review; create a fresh proposal")

        issued, errors = [], []
        for comp in regimen["components"]:
            try:
                res = self.sim.draft_prescription(
                    patient_id=pid,
                    title=comp["drug"],
                    order={
                        "drug": comp["drug"], "dose": comp["dose"], "unit": comp["unit"],
                        "route": comp["route"], "frequency": comp["frequency"],
                        "duration": comp["duration"], "quantity": comp["quantity"],
                        "indication": (
                            f"{raw['phenotype']['display']} - switched from "
                            f"{raw['current_drug']} under {raw['rule_id']} "
                            f"v{raw['rule_version']}, authorised by {clinician}"
                        )[:2000],
                    },
                )
                issued.append(res.get("id"))
            except SimError as exc:
                errors.append(str(exc))
                break

        if errors:
            raw.update(status="incomplete", authorised_by=clinician, issued=issued,
                       cancelled=[], errors=errors)
            self.ledger.event("apply_incomplete", task=task_id, patient=pid,
                              issued=issued, errors=errors)
            self.ledger.save()
            return {"status": "incomplete", "clinician": clinician, "issued": issued,
                    "cancelled": [], "errors": errors}

        cancelled = self._cancel_repeat(raw, clinician, errors)

        if errors or not cancelled:
            raw.update(status="incomplete", authorised_by=clinician, issued=issued,
                       cancelled=cancelled, errors=errors)
            self.ledger.event("apply_incomplete", task=task_id, patient=pid,
                              issued=issued, cancelled=cancelled, errors=errors)
            self.ledger.save()
            return {"status": "incomplete", "clinician": clinician, "issued": issued,
                    "cancelled": cancelled, "errors": errors}

        try:
            self.sim.save_consultation(
                patient_id=pid,
                title=f"Pharmacogenomic switch - {raw['gene']}",
                text=(
                    f"{raw['phenotype']['display']}. {raw['summary']}\n"
                    f"Stopped: {raw['current_drug']}\n"
                    f"Replacement prescription drafts created: {regimen['label']}\n"
                    f"Rule {raw['rule_id']} v{raw['rule_version']}. "
                    f"Authorised by {clinician}." + (f"\nNote: {note}" if note else "")
                ),
            )
            self.sim.message_patient(
                patient_id=pid,
                subject="A change to your medicine",
                body=(
                    "Your practice has reviewed a genetic test result held in your "
                    "record and prepared replacement prescription drafts for "
                    f"{raw['current_drug']}. These still require the prescribing "
                    "service's issue process. Contact the practice to confirm when "
                    "and how to change your medicine. "
                    "(Simulated message - synthetic data.)"
                ),
            )
        except SimError as exc:
            errors.append(str(exc))

        raw["status"] = "incomplete" if errors else "applied"
        raw["authorised_by"] = clinician
        raw["issued"] = issued
        raw["cancelled"] = cancelled
        self.ledger.event("apply_incomplete" if errors else "applied", task=task_id, clinician=clinician,
                          patient=pid, issued=issued, cancelled=cancelled,
                          errors=errors)
        if not errors:
            self._close_task(task_id, raw, clinician)
        self.ledger.save()
        return {"status": raw["status"], "clinician": clinician, "issued": issued,
                "cancelled": cancelled, "errors": errors}

    def _already_switched(self, patient_id: str, regimen: dict) -> list[str]:
        """Live prescriptions that already match the proposed regimen."""
        wanted = {c["drug"].lower() for c in regimen.get("components", [])}
        if not wanted:
            return []
        existing = []
        try:
            for res in self.sim.resources("gp", patient=patient_id, limit=200, max_pages=6):
                if res.get("kind") != "prescription":
                    continue
                if res.get("status") in ("rejected", "cancelled"):
                    continue
                data = res.get("data") or {}
                drug = ((data.get("medicationOrder") or {}).get("drug")
                        or data.get("drug") or res.get("title") or "").lower()
                if drug in wanted:
                    existing.append(res.get("id", ""))
        except SimError as exc:
            self.ledger.event("record_error", patient=patient_id, error=str(exc))
            raise
        return existing

    def _cancel_repeat(self, raw: dict, clinician: str, errors: list[str]) -> list[str]:
        """Cancel the ineffective repeat so it cannot be re-issued."""
        cancelled = []
        rid = raw.get("current_resource_id")
        if rid:
            try:
                res = self.sim.cancel_prescription(
                    rid, raw.get("current_version", 1),
                    f"Cancelled: {raw['phenotype']['display']}. "
                    f"{raw['rule_id']} v{raw['rule_version']}. Authorised by {clinician}.",
                    owner=raw.get("current_owner") or "pharmacy",
                )
                if res.get("status") == "rejected":
                    cancelled.append(res.get("id", rid))
                else:
                    errors.append(f"cancel {rid}: ended in status {res.get('status')}")
            except SimError as exc:
                errors.append(f"cancel {rid}: {exc}")
        if not cancelled:
            # No cancellable prescription resource: the repeat lives on the
            # medication list, so record the stop explicitly as a task for the
            # practice rather than silently leaving it active.
            try:
                t = self.sim.create_task(
                    "gp", raw["patient_id"],
                    f"Stop repeat: {raw['current_drug']}",
                    f"{raw['phenotype']['display']} - {raw['current_drug']} is "
                    f"ineffective for this patient and has been replaced. "
                    f"Remove it from the repeat list. Authorised by {clinician}.",
                )
                errors.append(f"Repeat cancellation needs manual action: task {t.get('id')}")
            except SimError as exc:
                errors.append(f"stop-task: {exc}")
        return cancelled

    def _close_task(self, task_id: str, raw: dict, clinician: str) -> None:
        try:
            self.sim.complete_task("gp", task_id, raw.get("task_version", 1),
                                   f"Actioned by {clinician}.")
        except SimError:
            pass  # task lifecycle is cosmetic here; the ledger is the record

    # -- stage 7: chase ---------------------------------------------------
    def chase(self, after_ms: int = CHASE_AFTER_MS) -> list[dict[str, Any]]:
        """Escalate anything routed and unactioned past the deadline."""
        now = self.sim.now()
        chased = []
        for task_id, raw in self.ledger.data["proposals"].items():
            if raw.get("status") != "routed":
                continue
            routed_at = raw.get("routed_at") or 0
            age = now - routed_at
            if age < after_ms:
                continue
            # Chase once per interval, not once per call.
            last = raw.get("last_chased_at")
            if last and (now - last) < after_ms:
                continue
            n = raw.get("chase_count", 0) + 1
            raw["chase_count"] = n
            raw["last_chased_at"] = now
            hours = age / 3_600_000
            if not self.dry_run:
                try:
                    self.sim.create_task(
                        "gp", raw["patient_id"],
                        f"[CHASE {n}] Unactioned PGx alert - {raw['patient_name']}",
                        (f"The pharmacogenomic alert raised {hours:.0f} hours ago "
                         f"has not been actioned.\n\n"
                         f"{raw['phenotype']['display']} on {raw['current_drug']}.\n"
                         f"{raw['summary']}\n\n"
                         f"Original task: {task_id}\n"
                         + ("ESCALATED to the practice pharmacist for review."
                            if n >= 2 else "Please review.")),
                    )
                except SimError as exc:
                    self.ledger.event("chase_error", task=task_id, error=str(exc))
                    continue
            self.ledger.event("chased", task=task_id, attempt=n, age_hours=round(hours, 1))
            chased.append({"task": task_id, "attempt": n, "age_hours": round(hours, 1),
                           "patient": raw["patient_id"]})
        self.ledger.save()
        return chased

    # -- convenience ------------------------------------------------------
    def run_cycle(self, patient_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """One full pass. This is what a nightly job would call."""
        phenos = self.scan(patient_ids)
        coded = self.code(phenos)
        routed = []
        for pid in phenos:
            for prop in self.hook(pid):
                routed.append(self.route(prop))
        chased = self.chase()
        self.ledger.save()
        return {
            "patients_with_findings": len(phenos),
            "phenotypes_coded": len(coded),
            "proposals_routed": len(routed),
            "chased": len(chased),
            "proposals": routed,
        }
