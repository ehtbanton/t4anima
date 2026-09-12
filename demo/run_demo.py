"""End-to-end demonstration against the NHS-SIM world.

  python3 demo/seed.py          # once: create the cohort
  python3 demo/run_demo.py      # the agent working the pathway

Shows, in order: extraction from free text, coding to the primary care record,
the prescribing hook firing, the safety layer changing the answer, the GP's
one-click decision, authorisation by a named clinician, cancellation of the
ineffective repeat, and the 48-hour chase.
"""
from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pgxbridge.agent import PgxAgent, Ledger
from pgxbridge.sim import SimClient
from pgxbridge import registry

COHORT = [f"SIM-0000{n}" for n in range(11, 23)]
CLINICIAN = "Dr Priya Raman (GMC 7654321)"


def rule(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def ts(ms: int) -> str:
    return datetime.datetime.fromtimestamp(ms / 1000, datetime.UTC).strftime("%d %b %Y %H:%M")


def main() -> int:
    key = os.environ.get("NHS_SIM_KEY")
    if not key:
        print("Set NHS_SIM_KEY first.", file=sys.stderr)
        return 2
    client = SimClient(key)
    ledger = Ledger(Path("pgx_demo_ledger.json"))
    agent = PgxAgent(client, ledger=ledger)

    stamp = registry.registry_stamp()
    print(f"PGx Bridge | world {client.team()['world']} | sim time {ts(client.now())}")
    print(f"gene registry {stamp['genes_version']} | "
          f"{len(registry.rule_sets())} rule sets | "
          f"{len(registry.all_watched_drugs())} drugs watched")

    rule("1. EXTRACTION - reading the secondary care record")
    phenos = agent.scan(COHORT)
    for pid in COHORT:
        got = phenos.get(pid)
        if not got:
            print(f"  {pid}  no pharmacogenomic assertion found")
            continue
        for p in got:
            print(f"  {pid}  {p.gene:8} {p.display:50} conf {p.confidence:.2f}")
            print(f"{'':14}from {p.sources[0].as_citation()}")
            print(f"{'':14}{p.derivation}")

    rule("2. PHENOTYPE STORE - coded into primary care, valid for life")
    for rec in agent.code(phenos):
        print(f"  {rec['patient_id']}  SNOMED {rec['snomed_code']}  {rec['display']}")
    ledger.save()

    rule("3. PRESCRIBING HOOK - stored phenotype meets current medicines")
    routed = []
    for pid in COHORT:
        props = agent.hook(pid)
        if not props:
            continue
        for p in props:
            p = agent.route(p)
            routed.append(p)
            kind = "ESCALATED" if p.blocked_by else "ONE-CLICK"
            print(f"  {pid}  [{p.severity:8}] {p.rule_id:24} {p.action:15} {kind:10} task {p.task_id}")
            if p.proposed_regimen:
                print(f"{'':14}-> {p.proposed_regimen['label']}")
            for b in p.blocked_by:
                print(f"{'':14}-> {b}")
    ledger.save()

    actionable = [p for p in routed if p.safe_to_offer and p.task_id]
    if actionable:
        rule("4. THE DECISION AS THE GP SEES IT")
        print(agent.render(actionable[0]))

        rule("5. AUTHORISATION - by a named clinician, never by the agent")
        target = actionable[0]
        try:
            agent.authorise(target.task_id, clinician="")
        except ValueError as exc:
            print(f"  unnamed authorisation refused: {exc}")
        res = agent.authorise(target.task_id, clinician=CLINICIAN,
                              note="Agreed at medicines optimisation review.")
        print(f"  authorised by  : {res['clinician']}")
        print(f"  issued         : {', '.join(res['issued'])}")
        print(f"  repeat cancelled: {', '.join(res['cancelled'])}")
        if res["errors"]:
            print(f"  errors         : {res['errors']}")

        after = [r for r in client.resources("gp", patient=target.patient_id)
                 if r["kind"] == "prescription"]
        print(f"\n  {target.patient_name}'s prescriptions now:")
        for r in after:
            print(f"    {r['id']:9} {str(r.get('title'))[:44]:46} {r['status']}")

    rule("6. THE CHASE - 48 hours later, for whatever nobody touched")
    print(f"  sim time now {ts(client.now())}; chase due at 48h")
    print(f"  chase before deadline : {len(agent.chase())} escalations")
    client.post("/api/clock", {"paused": True, "advanceMinutes": 4320})  # +72h
    client.post("/api/clock", {"paused": False, "speed": 60})
    print(f"  advanced to  {ts(client.now())}")
    for r in agent.chase():
        print(f"  CHASE {r['attempt']}  task {r['task']}  {r['age_hours']:.0f}h unactioned  {r['patient']}")
    print(f"  immediate re-chase     : {len(agent.chase())} (cooldown holds)")
    ledger.save()

    rule("SUMMARY")
    counts: dict[str, int] = {}
    for raw in ledger.data["proposals"].values():
        counts[raw.get("status", "?")] = counts.get(raw.get("status", "?"), 0) + 1
    print(f"  phenotypes coded : {len(ledger.data['phenotypes'])}")
    print(f"  proposals        : {counts}")
    print(f"  audit events     : {len(ledger.data['events'])}")
    print(f"  ledger           : {ledger.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
