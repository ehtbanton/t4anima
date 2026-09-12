"""Thin client for the NHS-SIM API.

Everything the agent touches in the simulated estate goes through here, so the
whole surface area against the record system is one auditable file. Swapping
this for GP Connect / EPS / a real EPR is the integration job; nothing above
this layer knows what system it is talking to.
"""
from __future__ import annotations

import os
import time
from typing import Any, Iterator

import requests

DEFAULT_BASE = "https://sim.animahacks.com"


class SimError(RuntimeError):
    pass


class SimClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, timeout: int = 60):
        self.api_key = api_key or os.environ.get("NHS_SIM_KEY", "")
        if not self.api_key:
            raise SimError("No API key. Set NHS_SIM_KEY or pass api_key=.")
        self.base = (base_url or os.environ.get("NHS_SIM_URL", DEFAULT_BASE)).rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })
        self.call_log: list[dict[str, Any]] = []

    # ---- plumbing -------------------------------------------------------
    def _request(self, method: str, path: str, **kw) -> Any:
        url = f"{self.base}{path}"
        for attempt in range(3):
            resp = self.session.request(method, url, timeout=self.timeout, **kw)
            if resp.status_code == 429 and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        self.call_log.append({"method": method, "path": path, "status": resp.status_code})
        if resp.status_code >= 400:
            raise SimError(f"{method} {path} -> {resp.status_code}: {resp.text[:400]}")
        if not resp.text:
            return None
        return resp.json()

    def get(self, path: str, **params) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        return self._request("GET", path, params=params)

    def post(self, path: str, payload: dict) -> Any:
        return self._request("POST", path, json=payload)

    # ---- reads ----------------------------------------------------------
    def team(self) -> dict:
        return self.get("/api/team")

    def clock(self) -> dict:
        return self.get("/api/clock")

    def now(self) -> int:
        return int(self.clock()["now"])

    def patients(self, site: str = "gp", q: str | None = None, offset: int = 0) -> dict:
        return self.get(f"/api/sites/{site}/patients", q=q, offset=offset)

    def patient(self, patient_id: str, site: str = "gp") -> dict | None:
        res = self.patients(site=site, q=patient_id)
        for item in res.get("items", []):
            if item["id"] == patient_id:
                return item
        return None

    def view(self, site: str, patient: str | None = None, offset: int = 0, limit: int = 200) -> dict:
        return self.get(f"/api/sites/{site}/view", patient=patient, offset=offset, limit=limit)

    def resources(self, site: str, patient: str | None = None, limit: int = 200,
                  max_pages: int = 25) -> Iterator[dict]:
        """Page through a site view, yielding resources."""
        offset = 0
        for _ in range(max_pages):
            page = self.view(site, patient=patient, offset=offset, limit=limit)
            items = page.get("resources", [])
            if not items:
                return
            yield from items
            offset += len(items)
            if offset >= page.get("resourceTotal", 0):
                return

    def hospital_documents(self) -> dict:
        return self.get("/api/sites/hospital/documents")

    def gp_documents(self) -> dict:
        return self.get("/api/sites/gp/documents")

    def pathology(self) -> Any:
        return self.get("/api/nhs/pathology")

    # ---- writes ---------------------------------------------------------
    def action(self, site: str, payload: dict) -> dict:
        return self.post(f"/api/sites/{site}/actions", payload)

    def save_problem(self, patient_id: str, title: str, code: str,
                     status: str = "active", onset: str | None = None) -> dict:
        payload = {
            "type": "save_problem",
            "patientId": patient_id,
            "title": title[:200],
            "problemStatus": status,
            "problemCode": code,
        }
        if onset:
            payload["onsetDate"] = onset
        return self.action("gp", payload)

    def save_consultation(self, patient_id: str, title: str, text: str) -> dict:
        return self.action("gp", {
            "type": "save_consultation",
            "patientId": patient_id,
            "title": title[:200],
            "text": text[:10000],
            "consultationStatus": "saved",
        })

    def create_task(self, site: str, patient_id: str, title: str, text: str,
                    priority: str | None = None) -> dict:
        payload = {
            "type": "create_task",
            "patientId": patient_id,
            "title": title[:200],
            "text": text[:10000],
        }
        return self.action(site, payload)

    def complete_task(self, site: str, resource_id: str, version: int, text: str) -> dict:
        return self.action(site, {
            "type": "complete",
            "resourceId": resource_id,
            "expectedVersion": version,
            "text": text[:10000],
        })

    def draft_prescription(self, patient_id: str, title: str, order: dict) -> dict:
        return self.action("gp", {
            "type": "draft_prescription",
            "patientId": patient_id,
            "title": title[:200],
            "medicationOrder": order,
        })

    def reject_resource(self, site: str, resource_id: str, version: int, text: str) -> dict:
        return self.action(site, {
            "type": "reject",
            "resourceId": resource_id,
            "expectedVersion": version,
            "text": text[:10000],
        })

    def cancel_prescription(self, resource_id: str, version: int, reason: str,
                            owner: str = "pharmacy") -> dict:
        """Cancel a live prescription.

        The estate will not reject a script straight from draft, and will not
        reject one that has already been approved. The supported path is
        review -> reject on the owning service, so that is what we walk.
        """
        try:
            reviewed = self.action(owner, {
                "type": "review", "resourceId": resource_id,
                "expectedVersion": version, "text": reason[:10000],
            })
            version = reviewed.get("version", version)
        except SimError:
            pass  # already reviewed, or not reviewable; try the reject anyway
        return self.action(owner, {
            "type": "reject", "resourceId": resource_id,
            "expectedVersion": version, "text": reason[:10000],
        })

    def message_patient(self, patient_id: str, subject: str, body: str) -> dict:
        return self.action("gp", {
            "type": "messaging_action",
            "patientId": patient_id,
            "messagingCommand": {
                "kind": "create",
                "subject": subject[:160],
                "body": body[:5000],
                "channel": "sms",
                "allowReply": True,
            },
        })

    def save_discharge_summary(self, patient_id: str, title: str, sections: dict) -> dict:
        return self.action("hospital", {
            "type": "save_discharge_summary",
            "patientId": patient_id,
            "title": title[:200],
            "dischargeSections": sections,
        })

    def process_document(self, site: str, resource_id: str, version: int, command: str,
                         text: str = "", snomed: list[dict] | None = None) -> dict:
        payload = {
            "type": "process_document",
            "resourceId": resource_id,
            "expectedVersion": version,
            "documentCommand": command,
        }
        if text:
            payload["text"] = text[:10000]
        if snomed:
            payload["documentSnomedCodes"] = snomed[:20]
        return self.action(site, payload)
