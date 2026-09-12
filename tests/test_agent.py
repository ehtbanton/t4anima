"""Agent orchestration tests against a fake estate. No network.

These cover the failure modes that would actually hurt someone: duplicate
prescribing, alert spam, acting without a named clinician, and a phenotype that
is already on the record being written twice.
"""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pgxbridge.agent import PgxAgent, Ledger
from pgxbridge.sim import SimError

DISCHARGE = {
    "id": "r-100", "kind": "discharge-summary", "title": "Stroke discharge",
    "patientId": "SIM-000001", "version": 1, "createdAt": 1_000,
    "data": {"sections": {
        "results": "CYP2C19 genotype *2/*2. Predicted phenotype: poor metaboliser.",
        "medicationChanges": "STARTED: Clopidogrel 75mg once daily.",
    }},
}

SCRIPT = {
    "id": "r-200", "kind": "prescription", "title": "Clopidogrel 75mg tablets",
    "patientId": "SIM-000001", "version": 1, "status": "draft", "owner": "pharmacy",
    "data": {"medicationOrder": {
        "drug": "Clopidogrel 75mg tablets", "dose": "75", "unit": "mg", "route": "oral",
        "frequency": "once daily", "duration": "28 days", "quantity": 28,
        "indication": "Secondary prevention after ischaemic stroke"}},
}


class FakeSim:
    """Minimal stand-in for the estate, with a controllable clock."""

    def __init__(self, resources=None, clock=1_000_000):
        resources = resources or []
        self.store = {"gp": copy.deepcopy(resources), "hospital": copy.deepcopy(resources),
                      "diagnostics": [], "community": []}
        self._now = clock
        self.actions: list[dict] = []
        self.fail_problem_with = None
        self._next = 1000

    def _id(self):
        self._next += 1
        return f"r-{self._next}"

    def now(self):
        return self._now

    def advance_hours(self, h):
        self._now += int(h * 3_600_000)

    def view(self, site, patient=None, offset=0, limit=200):
        items = [r for r in self.store.get(site, []) if not patient or r.get("patientId") == patient]
        return {"resources": items, "resourceTotal": len(items)}

    def resources(self, site, patient=None, limit=200, max_pages=25):
        yield from self.view(site, patient)["resources"]

    def patient(self, pid, site="gp"):
        return {"id": pid, "name": "Test Patient", "conditions": []}

    def hospital_documents(self):
        return {"resources": self.store["hospital"]}

    def gp_documents(self):
        return {"resources": self.store["gp"]}

    def save_problem(self, patient_id, title, code, status="active", onset=None):
        if self.fail_problem_with:
            raise SimError(self.fail_problem_with)
        res = {"id": self._id(), "kind": "problem", "title": title, "patientId": patient_id,
               "version": 1, "status": status, "data": {"code": code}}
        self.store["gp"].append(res)
        self.actions.append({"type": "save_problem", "code": code})
        return res

    def save_consultation(self, patient_id, title, text):
        self.actions.append({"type": "save_consultation", "title": title})
        return {"id": self._id()}

    def create_task(self, site, patient_id, title, text, priority=None):
        res = {"id": self._id(), "kind": "task", "title": title, "patientId": patient_id,
               "version": 1, "status": "open", "createdAt": self._now}
        self.store["gp"].append(res)
        self.actions.append({"type": "create_task", "title": title})
        return res

    def complete_task(self, site, resource_id, version, text):
        return {"id": resource_id}

    def draft_prescription(self, patient_id, title, order):
        res = {"id": self._id(), "kind": "prescription", "title": title,
               "patientId": patient_id, "version": 1, "status": "draft", "owner": "pharmacy",
               "data": {"medicationOrder": order}}
        self.store["gp"].append(res)
        self.actions.append({"type": "draft_prescription", "drug": order["drug"]})
        return res

    def cancel_prescription(self, resource_id, version, reason, owner="pharmacy"):
        for r in self.store["gp"]:
            if r["id"] == resource_id:
                r["status"] = "rejected"
                self.actions.append({"type": "cancel", "id": resource_id})
                return r
        raise SimError("not found")

    def message_patient(self, patient_id, subject, body):
        self.actions.append({"type": "message", "subject": subject})
        return {"id": self._id()}


def make_agent(resources=None):
    """Fresh agent over a fresh copy of the fixtures.

    Deep-copied: the fake mutates resource status in place, and shared fixtures
    would leak state between tests.
    """
    tmp = Path(tempfile.mkdtemp()) / "ledger.json"
    sim = FakeSim(copy.deepcopy(resources or []))
    return PgxAgent(sim, ledger=Ledger(tmp)), sim


class TestCodingIsIdempotent(unittest.TestCase):
    def test_phenotype_coded_once(self):
        agent, sim = make_agent([DISCHARGE])
        agent.code(agent.scan(["SIM-000001"]))
        agent.code(agent.scan(["SIM-000001"]))
        self.assertEqual(sum(1 for a in sim.actions if a["type"] == "save_problem"), 1)

    def test_existing_problem_is_adopted_not_duplicated(self):
        agent, sim = make_agent([DISCHARGE])
        phenos = agent.scan(["SIM-000001"])
        code = phenos["SIM-000001"][0].snomed_code
        sim.store["gp"].append({"id": "r-999", "kind": "problem", "patientId": "SIM-000001",
                                "version": 1, "title": "pre-existing", "data": {"code": code}})
        agent.code(phenos)
        self.assertEqual([a for a in sim.actions if a["type"] == "save_problem"], [])
        self.assertTrue(agent.stored_phenotypes("SIM-000001"))

    def test_race_on_already_active_is_not_an_error(self):
        agent, sim = make_agent([DISCHARGE])
        sim.fail_problem_with = "409: This problem is already active. Edit the existing problem"
        agent.code(agent.scan(["SIM-000001"]))
        self.assertTrue(agent.stored_phenotypes("SIM-000001"))
        self.assertFalse([e for e in agent.ledger.data["events"] if e["kind"] == "code_error"])


class TestHookAndRouting(unittest.TestCase):
    def setUp(self):
        self.agent, self.sim = make_agent([DISCHARGE, SCRIPT])
        self.agent.code(self.agent.scan(["SIM-000001"]))

    def test_hook_fires_and_drafts_a_switch(self):
        props = self.agent.hook("SIM-000001")
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].action, "switch")
        self.assertEqual(props[0].proposed_regimen["regimen"], "aspirin_dipyridamole")

    def test_routing_is_idempotent(self):
        p1 = self.agent.route(self.agent.hook("SIM-000001")[0])
        p2 = self.agent.route(self.agent.hook("SIM-000001")[0])
        self.assertEqual(p1.task_id, p2.task_id)
        self.assertEqual(sum(1 for a in self.sim.actions if a["type"] == "create_task"), 1)

    def test_no_phenotype_means_no_alert(self):
        agent, _ = make_agent([SCRIPT])
        self.assertEqual(agent.hook("SIM-000001"), [])


class TestAuthorisation(unittest.TestCase):
    def setUp(self):
        self.agent, self.sim = make_agent([DISCHARGE, SCRIPT])
        self.agent.code(self.agent.scan(["SIM-000001"]))
        self.prop = self.agent.route(self.agent.hook("SIM-000001")[0])

    def test_requires_a_named_clinician(self):
        with self.assertRaises(ValueError):
            self.agent.authorise(self.prop.task_id, clinician="")
        with self.assertRaises(ValueError):
            self.agent.authorise(self.prop.task_id, clinician="   ")

    def test_nothing_is_prescribed_before_authorisation(self):
        self.assertEqual([a for a in self.sim.actions if a["type"] == "draft_prescription"], [])
        self.assertEqual([a for a in self.sim.actions if a["type"] == "cancel"], [])

    def test_authorisation_issues_and_cancels(self):
        res = self.agent.authorise(self.prop.task_id, clinician="Dr A")
        self.assertEqual(res["status"], "applied")
        self.assertEqual(len(res["issued"]), 2)
        self.assertEqual(res["cancelled"], ["r-200"])
        self.assertEqual(next(r for r in self.sim.store["gp"] if r["id"] == "r-200")["status"],
                         "rejected")

    def test_rejection_changes_nothing(self):
        res = self.agent.authorise(self.prop.task_id, clinician="Dr A", approve=False)
        self.assertEqual(res["status"], "rejected")
        self.assertEqual([a for a in self.sim.actions if a["type"] == "draft_prescription"], [])

    def test_double_authorisation_does_not_double_prescribe(self):
        self.agent.authorise(self.prop.task_id, clinician="Dr A")
        before = sum(1 for a in self.sim.actions if a["type"] == "draft_prescription")
        self.agent.authorise(self.prop.task_id, clinician="Dr A")
        self.assertEqual(sum(1 for a in self.sim.actions if a["type"] == "draft_prescription"),
                         before)

    def test_switch_already_on_record_is_superseded(self):
        self.sim.store["gp"].append({
            "id": "r-777", "kind": "prescription", "patientId": "SIM-000001", "version": 1,
            "status": "draft", "title": "Aspirin 75mg gastro-resistant tablets",
            "data": {"medicationOrder": {"drug": "Aspirin 75mg gastro-resistant tablets"}}})
        res = self.agent.authorise(self.prop.task_id, clinician="Dr A")
        self.assertEqual(res["status"], "superseded")
        self.assertEqual([a for a in self.sim.actions if a["type"] == "draft_prescription"], [])

    def test_patient_is_told(self):
        self.agent.authorise(self.prop.task_id, clinician="Dr A")
        self.assertTrue([a for a in self.sim.actions if a["type"] == "message"])


class TestChase(unittest.TestCase):
    def setUp(self):
        self.agent, self.sim = make_agent([DISCHARGE, SCRIPT])
        self.agent.code(self.agent.scan(["SIM-000001"]))
        self.agent.route(self.agent.hook("SIM-000001")[0])

    def test_nothing_chased_before_48h(self):
        self.sim.advance_hours(47)
        self.assertEqual(self.agent.chase(), [])

    def test_chased_after_48h(self):
        self.sim.advance_hours(49)
        self.assertEqual(len(self.agent.chase()), 1)

    def test_cooldown_prevents_spam(self):
        self.sim.advance_hours(49)
        self.agent.chase()
        self.assertEqual(self.agent.chase(), [])
        self.sim.advance_hours(49)
        self.assertEqual(len(self.agent.chase()), 1)

    def test_actioned_proposals_are_not_chased(self):
        task = list(self.agent.ledger.data["proposals"])[0]
        self.agent.authorise(task, clinician="Dr A")
        self.sim.advance_hours(72)
        self.assertEqual(self.agent.chase(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
