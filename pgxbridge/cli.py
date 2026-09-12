"""Command line for PGx Bridge.

  pgx scan      --patients SIM-000011,...    extract and code phenotypes
  pgx hook      --patient SIM-000011         fire stored phenotypes at current meds
  pgx run       --patients ...               full cycle: scan, code, draft, route, chase
  pgx show      --task r-1234                print the decision as the GP sees it
  pgx authorise --task r-1234 --clinician "Dr X"   apply (or --reject)
  pgx chase                                  escalate anything unactioned past 48h
  pgx status                                 ledger summary
  pgx registry                               versions of every rule set in force
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .agent import PgxAgent, Ledger
from .extract import PatternExtractor
from .sim import SimClient, SimError
from . import registry


def _agent(args) -> PgxAgent:
    client = SimClient(args.key or os.environ.get("NHS_SIM_KEY"), base_url=args.url)
    return PgxAgent(client, extractors=[PatternExtractor()],
                    ledger=Ledger(args.ledger), dry_run=getattr(args, "dry_run", False))


def _ids(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def cmd_scan(args) -> int:
    a = _agent(args)
    phenos = a.scan(_ids(args.patients))
    coded = a.code(phenos)
    a.ledger.save()
    for pid, ps in sorted(phenos.items()):
        for p in ps:
            print(f"{pid}  {p.gene:8} {p.display:52} "
                  f"{'diplotype ' + p.diplotype if p.diplotype else '':26} "
                  f"conf {p.confidence:.2f}")
    print(f"\n{len(phenos)} patients with findings, {len(coded)} newly coded.")
    return 0


def cmd_hook(args) -> int:
    a = _agent(args)
    props = a.hook(args.patient)
    if not props:
        print(f"{args.patient}: no stored phenotype fires against current medicines.")
        return 0
    for p in props:
        print(a.render(p))
        print()
        if args.route:
            p = a.route(p)
            print(f"[routed as task {p.task_id}]\n")
    a.ledger.save()
    return 0


def cmd_run(args) -> int:
    a = _agent(args)
    out = a.run_cycle(_ids(args.patients))
    print(f"patients with findings : {out['patients_with_findings']}")
    print(f"phenotypes coded       : {out['phenotypes_coded']}")
    print(f"proposals routed       : {out['proposals_routed']}")
    print(f"chased                 : {out['chased']}")
    for p in out["proposals"]:
        flag = "ESCALATED" if p.blocked_by else "one-click"
        print(f"  {p.patient_id}  [{p.severity:8}] {p.rule_id:24} {flag:10} task {p.task_id}")
    return 0


def cmd_show(args) -> int:
    led = Ledger(args.ledger)
    raw = led.data["proposals"].get(args.task)
    if not raw:
        print(f"no proposal for task {args.task}", file=sys.stderr)
        return 1
    print(json.dumps(raw, indent=2, default=str))
    return 0


def cmd_authorise(args) -> int:
    a = _agent(args)
    try:
        res = a.authorise(args.task, clinician=args.clinician,
                          approve=not args.reject, note=args.note or "")
    except (ValueError, KeyError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2))
    return 0


def cmd_chase(args) -> int:
    a = _agent(args)
    out = a.chase(after_ms=args.after_hours * 3_600_000)
    if not out:
        print("nothing due for chase.")
    for r in out:
        print(f"chase {r['attempt']}  task {r['task']}  {r['age_hours']}h  {r['patient']}")
    return 0


def cmd_status(args) -> int:
    led = Ledger(args.ledger)
    props = led.data["proposals"]
    counts: dict[str, int] = {}
    for raw in props.values():
        counts[raw.get("status", "?")] = counts.get(raw.get("status", "?"), 0) + 1
    print(f"phenotypes stored : {len(led.data['phenotypes'])}")
    print(f"proposals         : {len(props)}")
    for k, v in sorted(counts.items()):
        print(f"  {k:12} {v}")
    print(f"events            : {len(led.data['events'])}")
    return 0


def cmd_registry(args) -> int:
    stamp = registry.registry_stamp()
    print(f"gene registry     : {stamp.pop('genes_version')}")
    g = registry.genes()
    if not g.get("codes_validated"):
        print("  WARNING: SNOMED codes are PLACEHOLDERS pending validation.")
    print("rule sets in force:")
    for rid, ver in sorted(stamp.items()):
        print(f"  {rid:26} v{ver}")
    print(f"\ndrugs watched: {', '.join(sorted(registry.all_watched_drugs()))}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pgx", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--key", help="NHS-SIM API key (default: $NHS_SIM_KEY)")
    ap.add_argument("--url", help="API base URL (default: $NHS_SIM_URL)")
    ap.add_argument("--ledger", default="pgx_ledger.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan"); s.add_argument("--patients"); s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("hook"); s.add_argument("--patient", required=True); s.add_argument("--route", action="store_true"); s.set_defaults(fn=cmd_hook)
    s = sub.add_parser("run"); s.add_argument("--patients"); s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("show"); s.add_argument("--task", required=True); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("authorise"); s.add_argument("--task", required=True)
    s.add_argument("--clinician", required=True); s.add_argument("--reject", action="store_true")
    s.add_argument("--note"); s.set_defaults(fn=cmd_authorise)
    s = sub.add_parser("chase"); s.add_argument("--after-hours", type=float, default=48.0); s.set_defaults(fn=cmd_chase)
    s = sub.add_parser("status"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("registry"); s.set_defaults(fn=cmd_registry)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except SimError as exc:
        print(f"API error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
