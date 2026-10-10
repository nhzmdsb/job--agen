import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from job_agent.jobs import classify
from job_agent.store import Store, Conflict
from job_agent.sync import Sync, record_path
from job_agent.web import create_app
from job_agent.collect import extract, DEFAULT_URL


class MemoryRemote:
    """Failure injection only; not evidence of actual GitHub acceptance."""
    def __init__(self):
        self.files = {}
        self.blobs = {}
        self.offline = False

    def inventory(self):
        if self.offline:
            raise RuntimeError("offline test")
        return dict(self.files)

    def read(self, sha):
        return self.blobs[sha]

    def write(self, path, content, sha=None):
        if self.files.get(path) != sha:
            raise Conflict("remote CAS")
        new = hashlib.sha256(content).hexdigest()
        self.files[path] = new
        self.blobs[new] = content
        return new


class WorkbenchTests(unittest.TestCase):
    def test_official_extraction_malformed_metadata_and_plain_page(self):
        html = '<script type="application/ld+json">[null, "invalid"]</script><p>We are hiring</p><p>2025 and 2026 graduates</p><p>Apply</p>'
        result = extract(html, DEFAULT_URL)
        self.assertIn("2025 and 2026 graduates", result["jd"])
        self.assertNotIn("Apply", result["jd"])
        with self.assertRaises(ValueError):
            extract(html, "https://example.invalid/no-job")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = Store(self.home / "workspace.sqlite3")
        self.app = create_app(self.store, self.home)
        self.client = self.app.test_client()
        self.headers = {"X-Workspace": "1"}

    def job(self, suffix="a", **data):
        return self.store.put("jobs", {"company": "Fictional Test Company", "title": "Test role", "url": "https://example.invalid/" + suffix, **data}, suffix)

    def test_notes_cas_restart_unknown_fields_outbox(self):
        job = self.job(extra={"keep": True})
        url = "/api/records/jobs/a"
        response = self.client.patch(url, json={"expected_version": 1, "patch": {"notes": "saved notes"}}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        conflict = self.client.patch(url, json={"expected_version": 1, "patch": {"notes": "stale edit"}}, headers=self.headers)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json["current"]["data"]["notes"], "saved notes")
        reopened = Store(self.store.path).get("jobs", job["id"])
        self.assertEqual(reopened["data"]["extra"], {"keep": True})
        self.assertEqual(reopened["data"]["notes"], "saved notes")
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT version FROM outbox").fetchone()[0], 2)

    def test_colors_trash_previous_url_and_quota_ended(self):
        self.assertEqual([classify(x) for x in ({}, {"jd": "x", "qualification": "disadvantage"}, {"jd": "x", "qualification": "eligible"}, {"jd": "x", "qualification": "eligible", "interest": "yes"})], ["grey", "yellow", "beige", "green"])
        a = self.job(stage="trash")
        changed = self.store.put("jobs", {**a["data"], "url": "https://example.invalid/new"}, "a", 1)
        with self.assertRaises(Conflict):
            self.job("duplicate", url="https://example.invalid/a")
        self.assertEqual(changed["data"]["stage"], "trash")
        self.store.put("profiles", {"items": [{"company": "Fictional Test Company", "cycle": "2026", "limit": 1}]}, "company-quotas")
        b = self.job("b", stage="applied", cycle="2026")
        self.store.put("jobs", {**b["data"], "stage": "ended", "outcome": "rejected"}, "b", 1)
        c = self.job("c", cycle="2026")
        with self.assertRaises(Conflict):
            self.store.put("jobs", {**c["data"], "stage": "applied"}, "c", 1)
        confirmed = self.store.put("jobs", {**c["data"], "stage": "applied"}, "c", 1, quota_confirm=True, quota_reason="HR explicitly allowed second role")
        self.assertTrue(confirmed["data"]["quota_override_reason"])
        with self.assertRaises(ValueError):
            self.store.put("profiles", {"items": [{"company": "X", "cycle": "2026", "limit": True}]}, "company-quotas", 1)
        self.assertEqual(self.client.put("/api/quotas", json={"expected_version": 1, "items": [None]}, headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post("/api/jobs", json={"data": {"company": 123, "title": "x"}}, headers=self.headers).status_code, 400)

    def test_files_prompt_and_local_request_guard(self):
        self.job(jd="Actual context fixture", notes="Contact note")
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("word/document.xml", "<document>Fictional test resume</document>")
            z.writestr("[Content_Types].xml", "<Types/>")
        contents = [b"%PDF-1.4\nFictional test fixture", archive.getvalue()]
        for version, (extension, content) in enumerate(zip(("pdf", "docx"), contents), 1):
            response = self.client.post("/api/jobs/a/artifacts", data={"expected_version": str(version), "file": (io.BytesIO(content), "fixture." + extension)}, headers=self.headers)
            self.assertEqual(response.status_code, 200)
            download = self.client.get("/api/artifacts/" + response.json["artifact"]["id"] + "/download")
            self.assertEqual(hashlib.sha256(download.data).hexdigest(), hashlib.sha256(content).hexdigest())
            download.close()
        response = self.client.post("/api/jobs/a/prompt", json={"task": "resume", "description": "My request"}, headers=self.headers)
        self.assertIn("Actual context fixture", response.json["prompt"])
        self.assertIn("Contact note", response.json["prompt"])
        self.assertIn("另行上传", response.json["prompt"])
        self.assertEqual(self.client.post("/api/jobs", json={"data": {}}, headers={"X-Workspace": "1", "Origin": "https://evil.invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/", headers={"Host": "evil.invalid"}).status_code, 403)

    def test_exact_history_restore_atomic_failure(self):
        a = self.job(extra="unknown field")
        self.store.put("jobs", {**a["data"], "notes": "revision two"}, "a", 1)
        snapshot = self.store.export()
        restored = Store(self.home / "restored.sqlite3")
        self.assertEqual(restored.restore(snapshot), {"imported": 1})
        self.assertEqual(restored.export(), snapshot)
        malformed = copy.deepcopy(snapshot)
        malformed["history"][0]["version"] = 10
        blank = Store(self.home / "blank.sqlite3")
        with self.assertRaises(ValueError):
            blank.restore(malformed)
        self.assertEqual(blank.export()["records"], [])

    def test_sync_retry_independent_edits_and_conflict_resolution(self):
        remote = MemoryRemote()
        (self.home / "settings.json").write_text(json.dumps({"repository": "private/test"}))
        sync = self.app.extensions["sync"]
        sync.remote_factory = lambda _: remote
        a = self.job(notes="base")
        self.assertEqual(sync.run()["pending"], 0)
        other_home = self.home / "other"
        other_home.mkdir()
        (other_home / "settings.json").write_text(json.dumps({"repository": "private/test"}))
        other_store = Store(other_home / "db.sqlite3")
        other_sync = Sync(other_store, other_home, lambda _: remote)
        self.assertEqual(other_sync.run()["state"], "connected")
        remote.offline = True
        self.store.put("jobs", {**a["data"], "notes": "local branch"}, "a", 1)
        self.assertEqual(sync.run()["state"], "error")
        self.assertEqual(sync.status()["pending"], 1)
        remote.offline = False
        other_store.put("jobs", {**a["data"], "notes": "remote branch"}, "a", 1)
        other_store.put("leads", {"new": True}, "independent")
        other_sync.run()
        result = sync.run()
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertTrue(self.store.get("leads", "independent")["data"]["new"])
        chosen = self.store.get("jobs", "a")["data"]
        response = self.client.post("/api/conflicts/jobs/a/resolve", json={"expected_version": 2, "data": {**chosen, "notes": "local branch\nremote branch"}}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(sync.run()["pending"], 0)
        pack = json.loads(remote.read(remote.inventory()[record_path("jobs", "a")]))
        self.assertEqual(pack["record"]["version"], 3)
        self.assertTrue(any(p.startswith("conflict-history/") for p in remote.inventory()))
        self.assertEqual(len(sync.status()["conflicts"]), 0)


if __name__ == "__main__":
    unittest.main()
