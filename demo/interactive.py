"""Local interactive PGx demonstration. Run: python3 -m demo.interactive.

Each browser session has its own synthetic estate. No network client is created;
all mutations, messages and clock changes remain inside this process.
"""
from __future__ import annotations

import argparse
import copy
import json
import secrets
import tempfile
import threading
import time
from dataclasses import asdict
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from demo.seed import CASES, ALLERGIES
from pgxbridge.agent import Ledger, PgxAgent
from pgxbridge.extract import PatternExtractor, scan_resource
from pgxbridge.sim import SimError

NAMES = ['Mei Khan', 'Eleanor Brooks', 'Daniel Okafor', 'Ravi Shah',
         'Oliver Reed', 'Sofia Rossi', 'Grace Williams', 'Arthur Clarke',
         'Amara Lewis', 'Henry Wilson', 'Isabel Morgan', 'James Patel']
STORIES = [
    'A result in a stroke discharge letter never reached the prescription.',
    'The result arrived after discharge, buried in a paragraph of narrative.',
    'The gene is never named. A variant identifier carries the answer.',
    'A dose adjustment belongs with oncology, not an automatic GP switch.',
    'A positive screening result calls for specialist review.',
    'The rule calls for a hold and a specialist decision.',
    'A normal result should be stored without creating an alert.',
    'A test request is not a result. The pipeline should stay silent.',
    'A relative’s genotype must never become this patient’s finding.',
    '“Not detected” must stay negative, even though it contains “detected”.',
    'The PCI indication changes which alternative the rule proposes.',
    'The result is actionable, but an aspirin allergy blocks the alternatives.',
]
BASE_TIME = 1789214400000


class DemoEstate:
    """In-memory adapter implementing the same surface used by PgxAgent."""
    def __init__(self):
        self.store = {site: [] for site in ('gp', 'hospital', 'diagnostics', 'community')}
        self.clock = BASE_TIME
        self.counter = 1000
        self.fail_next = False
        self.names = dict(zip((c[0] for c in CASES), NAMES))
        for index, (pid, _, sections, med) in enumerate(CASES):
            self.store['hospital'].append({
                'id': f'letter-{index}', 'kind': 'discharge-summary', 'patientId': pid,
                'title': 'Northbank General · discharge summary', 'version': 1,
                'createdAt': BASE_TIME - 365 * 86400000,
                'data': {'sections': copy.deepcopy(sections)}})
            self.store['gp'].append({
                'id': f'original-{index}', 'kind': 'prescription', 'patientId': pid,
                'title': med['drug'], 'status': 'active', 'version': 1, 'owner': 'gp',
                'data': {'medicationOrder': copy.deepcopy(med)}})
        for pid, substance, reaction in ALLERGIES:
            self.store['gp'].append({'id': 'seed-allergy', 'kind': 'allergy',
                                    'patientId': pid, 'title': substance,
                                    'status': 'active', 'data': {'reaction': reaction}})

    def now(self): return self.clock

    def patient(self, pid):
        return {'id': pid, 'name': self.names[pid], 'conditions': []}

    def resources(self, site, patient=None, **kwargs):
        return iter([r for r in self.store[site] if not patient or r['patientId'] == patient])

    def view(self, site, patient=None, offset=0, limit=200):
        rows = list(self.resources(site, patient))
        return {'resources': rows[offset:offset + limit], 'resourceTotal': len(rows)}

    def _add(self, site, patient_id, kind, title, data=None, status='saved'):
        self.counter += 1
        row = {'id': f'demo-{self.counter}', 'kind': kind, 'patientId': patient_id,
               'title': title, 'data': data or {}, 'status': status, 'version': 1,
               'createdAt': self.now(), 'owner': site}
        self.store[site].append(row)
        return row

    def save_problem(self, patient_id, title, code, status='active', **kw):
        return self._add('gp', patient_id, 'problem', title, {'code': code}, status)

    def save_consultation(self, patient_id, title, text):
        return self._add('gp', patient_id, 'consultation', title, {'text': text})

    def create_task(self, site, patient_id, title, text, **kw):
        return self._add(site, patient_id, 'task', title, {'text': text}, 'open')

    def complete_task(self, site, resource_id, version, text):
        row = next(r for r in self.store[site] if r['id'] == resource_id)
        row['status'] = 'completed'
        row['version'] += 1
        return row

    def draft_prescription(self, patient_id, title, order):
        if self.fail_next:
            self.fail_next = False
            raise SimError('Demo failure: replacement prescription service unavailable')
        return self._add('gp', patient_id, 'prescription', title,
                         {'medicationOrder': copy.deepcopy(order)}, 'draft')

    def cancel_prescription(self, resource_id, version, reason, owner='gp'):
        row = next(r for r in self.store['gp'] if r['id'] == resource_id)
        if row['version'] != version:
            raise SimError('Prescription changed since review')
        row['status'] = 'rejected'
        row['version'] += 1
        return row

    def message_patient(self, patient_id, subject, body):
        return self._add('gp', patient_id, 'message-preview', subject, {'text': body}, 'simulated')


class DemoSession:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pgx-demo-')
        self.sim = DemoEstate()
        self.agent = PgxAgent(self.sim, ledger=Ledger(Path(self.tmp.name) / 'ledger.json'))
        self.scanned = False
        self.timeline = []
        self.lock = threading.Lock()
        self.touched = time.monotonic()

    def event(self, text, pid=None):
        self.timeline.append({'at': self.sim.now(), 'text': text, 'patient_id': pid})

    def proposals(self, pid):
        return [p for p in self.agent.ledger.data['proposals'].values() if p['patient_id'] == pid]

    def scan(self):
        if self.scanned: return
        phenos = self.agent.scan([c[0] for c in CASES])
        self.agent.code(phenos)
        for pid, _, _, _ in CASES:
            for proposal in self.agent.hook(pid): self.agent.route(proposal)
        self.scanned = True
        self.event('12 records screened · 8 alerts · 4 negative controls stayed silent.')
        self.agent.ledger.save()

    def action(self, data):
        action = data.get('action')
        pid = data.get('patient_id')
        if action == 'scan':
            self.scan()
            return 'Screening complete. Review the evidence and choose a decision.'
        if action == 'advance':
            if not self.scanned: raise ValueError('Screen the cohort before advancing time.')
            self.sim.clock += 48 * 3600000
            chased = self.agent.chase()
            self.event(f'Clock advanced 48 hours · {len(chased)} outstanding alerts chased.')
            return f'{len(chased)} outstanding alerts chased. Actioned cases were excluded.'
        if action == 'chase':
            chased = self.agent.chase()
            self.event(f'Chase checked · {len(chased)} new reminders.')
            return f'{len(chased)} new reminders. A 48-hour cooldown prevents duplicates.'
        if pid not in self.sim.names: raise ValueError('Unknown patient.')
        proposals = self.proposals(pid)
        pending = next((p for p in reversed(proposals) if p['status'] == 'routed'), None)
        if not pending: raise ValueError('This patient has no pending decision.')
        if action in ('approve', 'reject', 'escalate'):
            clinician = str(data.get('clinician', '')).strip()
            note = str(data.get('note', '')).strip()
            if not clinician: raise ValueError('Enter a clinician name for this simulated decision.')
            if action != 'approve' and not note: raise ValueError('Add a reason for this decision.')
            if len(clinician) > 120 or len(note) > 2000: raise ValueError('Decision text is too long.')
            if action == 'escalate':
                pending.update(status='escalated', authorised_by=clinician, decision_note=note)
                self.sim.create_task('gp', pid, 'Specialist review requested', note)
                self.agent._close_task(pending['task_id'], pending, clinician)
                self.event(f'{clinician} requested specialist review. Medicines unchanged.', pid)
                self.agent.ledger.save()
                return 'Specialist review recorded. No medicines changed.'
            result = self.agent.authorise(pending['task_id'], clinician,
                                          approve=action == 'approve', note=note)
            pending['decision_note'] = note
            if action == 'reject':
                self.agent._close_task(pending['task_id'], pending, clinician)
                self.event(f'{clinician} rejected the proposed change. Medicines unchanged.', pid)
                return 'Proposal rejected. The reason is recorded; medicines are unchanged.'
            if result['status'] == 'applied':
                self.event(f'{clinician} approved · replacement drafts created · original cancelled · message preview saved.', pid)
                return 'Demo record updated. Replacement prescriptions are drafts, not issued medicines.'
            self.event(f'Change incomplete: {result["status"]}. Manual review needed.', pid)
            return 'Change incomplete. Review the record; no success message was generated.'
        if action == 'failure':
            self.sim.fail_next = not self.sim.fail_next
            return 'Next prescription write will fail.' if self.sim.fail_next else 'Prescription service restored.'
        if action == 'allergy':
            rid = f'whatif-{pid}'
            exists = any(r['id'] == rid for r in self.sim.store['gp'])
            self.sim.store['gp'] = [r for r in self.sim.store['gp'] if r['id'] != rid]
            if not exists:
                self.sim.store['gp'].append({'id': rid, 'kind': 'allergy', 'patientId': pid,
                                            'title': 'Aspirin', 'status': 'active', 'data': {}})
            # Leave the routed proposal unchanged: approval must recheck the new
            # record. Show the freshly recomputed safety next to the saved draft.
            self.event('What-if allergy ' + ('removed.' if exists else 'added after the proposal was drafted.'), pid)
            return 'Record updated. Safety has been recalculated; approval also rechecks the record.'
        raise ValueError('Unknown action.')

    def state(self):
        patients = []
        for i, (pid, label, sections, med) in enumerate(CASES):
            rows = list(self.sim.resources('gp', pid))
            proposals = self.proposals(pid)
            proposal = proposals[-1] if proposals else None
            fresh = self.agent.hook(pid) if self.scanned else []
            phenos = self.agent.stored_phenotypes(pid) if self.scanned else []
            source = self.sim.store['hospital'][i]
            evidence = [asdict(f) for f in scan_resource(source, [PatternExtractor()], 'hospital')] if self.scanned else []
            status = ('unscreened' if not self.scanned else 'no_alert' if not proposal else
                      ('blocked' if (fresh[0].blocked_by if fresh else proposal['blocked_by']) else 'ready') if proposal['status'] == 'routed'
                      else proposal['status'])
            patients.append({'id': pid, 'name': NAMES[i], 'story': STORIES[i], 'label': label,
                             'status': status, 'sections': sections, 'original': med,
                             'source': {'id': source['id'], 'title': source['title'], 'createdAt': source['createdAt']},
                             'phenotypes': [asdict(p) for p in phenos], 'evidence': evidence,
                             'proposal': proposal, 'fresh_proposal': fresh[0].to_dict() if fresh else None,
                             'records': rows, 'whatif_allergy': any(r['id'] == f'whatif-{pid}' for r in rows)})
        return {'patients': patients, 'scanned': self.scanned, 'now': self.sim.now(),
                'elapsed_hours': (self.sim.now() - BASE_TIME) // 3600000,
                'timeline': self.timeline, 'fail_next': self.sim.fail_next,
                'registry': self.agent.stamp,
                'audit': self.agent.ledger.data['events']}


STATIC = Path(__file__).parent / 'web'
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def session(self):
        cookies = SimpleCookie()
        try: cookies.load(self.headers.get('Cookie', ''))
        except Exception: pass
        token = cookies['pgx_demo'].value if 'pgx_demo' in cookies else ''
        with SESSIONS_LOCK:
            for old in list(SESSIONS):
                if time.monotonic() - SESSIONS[old].touched > 7200:
                    SESSIONS.pop(old).tmp.cleanup()
            if token not in SESSIONS:
                if len(SESSIONS) >= 32: raise ValueError('Demo session limit reached; restart the server.')
                token = secrets.token_urlsafe(24)
                SESSIONS[token] = DemoSession()
            s = SESSIONS[token]
            s.touched = time.monotonic()
        self.cookie = token
        return s

    def reply(self, code, value, content_type='application/json'):
        body = json.dumps(value).encode() if content_type == 'application/json' else value
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if getattr(self, 'cookie', None):
            self.send_header('Set-Cookie', f'pgx_demo={self.cookie}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/state':
            try:
                s = self.session()
                with s.lock: self.reply(200, s.state())
            except ValueError as exc: self.reply(400, {'error': str(exc)})
            return
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if path not in files:
            self.reply(404, {'error': 'Not found'})
            return
        filename, mime = files[path]
        self.reply(200, (STATIC / filename).read_bytes(), mime)

    def do_POST(self):
        if self.path != '/api/action': return self.reply(404, {'error': 'Not found'})
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.reply(403, {'error': 'Cross-origin request refused'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'JSON required'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size < 1 or size > 16000: raise ValueError('Invalid request size')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict): raise ValueError('Expected an object')
            s = self.session()
            with s.lock:
                if data.get('action') == 'reset':
                    with SESSIONS_LOCK:
                        replacement = DemoSession()
                        SESSIONS[self.cookie] = replacement
                    s.tmp.cleanup()
                    self.reply(200, {'message': 'Fresh synthetic cohort restored.', 'state': replacement.state()})
                else:
                    message = s.action(data)
                    self.reply(200, {'message': message, 'state': s.state()})
        except (ValueError, KeyError, SimError) as exc:
            self.reply(400, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'PGx Bridge interactive demo: http://127.0.0.1:{args.port}', flush=True)
    print('Synthetic local sandbox. No NHS-SIM connection or API key required.', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close()
        for session in SESSIONS.values(): session.tmp.cleanup()


if __name__ == '__main__': main()
