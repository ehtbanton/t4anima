"""Regression checks for the interactive demo and the failures found in review."""
import copy
import unittest
from demo.interactive import DemoSession
from pgxbridge.extract import PatternExtractor
from pgxbridge.models import SourceRef
from pgxbridge.phenotype import normalise
from pgxbridge.sim import SimError
from test_agent import make_agent, DISCHARGE, SCRIPT

PID = 'SIM-000011'


class TestDemoSession(unittest.TestCase):
    def setUp(self):
        self.session = DemoSession()
        self.addCleanup(self.session.tmp.cleanup)

    def scan(self):
        self.session.action({'action': 'scan'})
        return self.session.state()

    def decide(self, action='approve', pid=PID):
        return self.session.action({'action': action, 'patient_id': pid,
                                    'clinician': 'Dr Test (demo)', 'note': 'Test decision'})

    def test_cohort_expected_outcomes_and_idempotent_scan(self):
        state = self.scan()
        statuses = [p['status'] for p in state['patients']]
        self.assertEqual(statuses.count('ready'), 4)
        self.assertEqual(statuses.count('blocked'), 4)
        self.assertEqual(statuses.count('no_alert'), 4)
        count = len(self.session.sim.store['gp'])
        self.scan()
        self.assertEqual(len(self.session.sim.store['gp']), count)

    def test_approval_records_drafts_cancellation_and_preview(self):
        self.scan(); self.decide()
        patient = self.session.state()['patients'][0]
        self.assertEqual(patient['status'], 'applied')
        scripts = [r for r in patient['records'] if r['kind'] == 'prescription']
        self.assertEqual([r['status'] for r in scripts], ['rejected', 'draft', 'draft'])
        self.assertEqual(len([r for r in patient['records'] if r['kind'] == 'message-preview']), 1)
        with self.assertRaises(ValueError): self.decide()

    def test_blocked_dose_reduction_cannot_be_approved(self):
        self.scan()
        with self.assertRaises(ValueError): self.decide(pid='SIM-000014')
        row = next(r for r in self.session.sim.store['gp'] if r['id'] == 'original-3')
        self.assertEqual(row['status'], 'active')

    def test_changed_allergy_prevents_stale_approval(self):
        self.scan()
        self.session.action({'action': 'allergy', 'patient_id': PID})
        with self.assertRaises(ValueError): self.decide()
        self.assertEqual(self.session.state()['patients'][0]['records'][0]['status'], 'active')
        self.session.action({'action': 'allergy', 'patient_id': PID})
        self.decide()
        self.assertEqual(self.session.state()['patients'][0]['status'], 'applied')

    def test_failed_write_keeps_original_and_has_no_message(self):
        self.scan()
        self.session.action({'action': 'failure', 'patient_id': PID})
        self.decide()
        patient = self.session.state()['patients'][0]
        self.assertEqual(patient['status'], 'incomplete')
        self.assertEqual(patient['records'][0]['status'], 'active')
        self.assertFalse(any(r['kind'] == 'message-preview' for r in patient['records']))

    def test_reject_and_escalate_leave_medicines_unchanged(self):
        self.scan(); self.decide('reject'); self.decide('escalate', 'SIM-000014')
        state = self.session.state()
        self.assertEqual(state['patients'][0]['status'], 'rejected')
        self.assertEqual(state['patients'][3]['status'], 'escalated')
        for index in [0, 3]: self.assertEqual(state['patients'][index]['records'][0]['status'], 'active')

    def test_chase_omits_decisions_and_respects_cooldown(self):
        self.scan(); self.decide(); self.decide('reject', 'SIM-000012')
        message = self.session.action({'action': 'advance'})
        self.assertIn('6 outstanding alerts', message)
        self.assertIn('0 new reminders', self.session.action({'action': 'chase'}))

    def test_sessions_are_isolated(self):
        other = DemoSession(); self.addCleanup(other.tmp.cleanup)
        self.scan(); self.decide()
        self.assertFalse(other.scanned)
        self.assertEqual(other.sim.store['gp'][0]['status'], 'active')


class TestSafetyRegressions(unittest.TestCase):
    def setup_agent(self):
        agent, sim = make_agent([DISCHARGE, SCRIPT])
        agent.code(agent.scan(['SIM-000001']))
        prop = agent.route(agent.hook('SIM-000001')[0])
        return agent, sim, prop

    def test_interaction_in_prescription_resources_is_blocking(self):
        agent, sim, prop = self.setup_agent()
        other = copy.deepcopy(SCRIPT)
        other['id'] = 'apixaban'
        other['data']['medicationOrder']['drug'] = 'Apixaban 5mg tablets'
        sim.store['gp'].append(other)
        self.assertFalse(agent.hook('SIM-000001')[0].safe_to_offer)
        with self.assertRaises(ValueError): agent.authorise(prop.task_id, 'Dr Test')

    def test_second_write_failure_does_not_cancel_original_or_send_message(self):
        agent, sim, prop = self.setup_agent()
        original = sim.draft_prescription
        count = 0
        def fail_second(**kwargs):
            nonlocal count
            count += 1
            if count == 2: raise SimError('second write failed')
            return original(**kwargs)
        sim.draft_prescription = fail_second
        result = agent.authorise(prop.task_id, 'Dr Test')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(len(result['issued']), 1)
        self.assertFalse(result['cancelled'])
        self.assertFalse(any(a['type'] == 'message' for a in sim.actions))
        agent.authorise(prop.task_id, 'Dr Test')
        self.assertEqual(count, 2)

    def test_cancellation_failure_is_not_reported_as_cancelled(self):
        agent, sim, prop = self.setup_agent()
        def fail(*args, **kwargs): raise SimError('cancel failed')
        sim.cancel_prescription = fail
        result = agent.authorise(prop.task_id, 'Dr Test')
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['cancelled'], [])
        self.assertFalse(any(a['type'] == 'message' for a in sim.actions))

    def test_unavailable_record_does_not_pass_safety(self):
        agent, sim, prop = self.setup_agent()
        def fail(*args, **kwargs): raise SimError('read failed')
        sim.resources = fail
        with self.assertRaises(SimError): agent.authorise(prop.task_id, 'Dr Test')
        self.assertFalse(any(a['type'] == 'draft_prescription' for a in sim.actions))

    def test_extraction_regressions(self):
        ex = PatternExtractor()
        source = SourceRef('gp', 'test', 'report', 'Test')
        def extract(text): return normalise(ex.extract(text, source, 'test'))
        self.assertEqual(extract('CYP2C19 *2/*2 not detected.'), [])
        self.assertEqual(extract('HLA-B*57:01 screening discussed.'), [])
        phenos = extract('CYP2C19 *1/*1; TPMT *3A/*3C.')
        self.assertEqual([(p.gene, p.diplotype) for p in phenos],
                         [('CYP2C19', '*1/*1'), ('TPMT', '*3A/*3C')])


if __name__ == '__main__': unittest.main()
