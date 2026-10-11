import copy
import tempfile
import unittest
from pathlib import Path

from job_agent.review import DIMENSIONS, content_hash, project, review_queue, confirm_proposal
from job_agent.store import Store, Conflict
from job_agent.web import create_app


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "db.sqlite3")
        self.profile = self.store.put("profiles", {"confirmed": {"major": "Fictional major", "unknown_field": True}, "preferences": {}}, "candidate")
        self.job = self.store.put("jobs", {"company": "Fictional company", "title": "Graduate trainee", "jd": "2026 graduates. Related majors preferred. Internships accepted. One year formal work required.", "notes": "user note", "extra": {"keep": True}}, "fixture")

    def work(self, decision="unknown", fail_kind=None):
        data = copy.deepcopy(self.job["data"])
        checks = [{"dimension": d, "kind": "unknown", "status": "unknown", "reason": "Not confirmed", "jd_quote": "", "profile_fact": "Not confirmed"} for d in DIMENSIONS]
        if fail_kind:
            checks[3].update(kind=fail_kind, status="fail", jd_quote="One year formal work required.", profile_fact="Fixture: no formal experience", reason="Fixture evidence")
        data["screening"] = {"auxiliary_unknown": "preserve", "work": {"decision": decision, "reason": "Fixture assessment", "checks": checks, "questions": ["Confirm facts"], "profile_version": self.profile["version"], "job_hash": content_hash(data), "assessed_at": "2026-10-11T00:00:00Z"}}
        return data

    def save(self, data):
        self.job = self.store.put("jobs", data, "fixture", self.job["version"])
        return project(self.job, self.profile)

    def test_mandatory_preferred_unknown_and_stage_independence(self):
        self.assertTrue(self.save(self.work("ineligible", "mandatory"))["view"]["low_priority"])
        self.assertEqual(self.job["data"].get("stage", "pool"), "pool")
        self.assertFalse(self.save({**self.job["data"], "pool_visibility": "show"})["view"]["low_priority"])
        self.assertFalse(self.save({**self.job["data"], "stage": "preparing", "pool_visibility": "auto"})["view"]["low_priority"])
        preferred = self.work("disadvantage", "preferred")
        preferred["stage"] = "pool"
        self.assertEqual(self.save(preferred)["view"]["color"], "yellow")
        self.assertFalse(project(self.job, self.profile)["view"]["low_priority"])
        self.assertEqual(self.save(self.work())["view"]["color"], "grey")

    def test_evidence_contract_and_profile_conflict(self):
        for dimension, quote in (("graduation", "2026 graduates."), ("major", "Related majors preferred."), ("experience", "Internships accepted.")):
            data = self.work("eligible")
            check = next(c for c in data["screening"]["work"]["checks"] if c["dimension"] == dimension)
            check.update(status="pass", kind="preferred", jd_quote=quote, profile_fact="Explicit fixture fact")
            self.save(data)
        bad = self.work("ineligible", "preferred")
        with self.assertRaises(ValueError):
            self.save(bad)
        bad = self.work()
        bad["screening"]["work"]["checks"][0]["jd_quote"] = "Fabricated JD"
        with self.assertRaises(ValueError):
            self.save(bad)
        old = self.work()
        self.profile = self.store.put("profiles", {**self.profile["data"], "preferences": {"changed": True}}, "candidate", 1)
        with self.assertRaises(Conflict):
            self.save(old)

    def test_staleness_notes_manual_interest_and_translation(self):
        self.save(self.work("eligible"))
        self.assertEqual(project(self.job, self.profile)["view"]["color"], "beige")
        self.assertTrue(self.save({**self.job["data"], "notes": "changed"})["view"]["fresh"])
        self.assertEqual(self.save({**self.job["data"], "interest": "yes"})["view"]["color"], "green")
        translated = {**self.job["data"], "translation": {"source_hash": content_hash(self.job["data"]), "title_zh": "虚构测试岗位", "summary_zh": "虚构测试摘要"}}
        self.assertTrue(self.save(translated)["view"]["translation_valid"])
        stale = self.save({**self.job["data"], "jd": self.job["data"]["jd"] + " Changed requirement."})
        self.assertTrue(stale["view"]["stale"])
        self.assertEqual(stale["view"]["color"], "grey")
        self.assertFalse(stale["view"]["translation_valid"])
        self.assertFalse(stale["view"]["low_priority"])
        manual = self.save({**self.job["data"], "color": "yellow", "color_reason": "User decision"})
        self.assertEqual(manual["view"]["origin"], "人工调整")
        self.assertEqual(manual["data"]["extra"], {"keep": True})
        self.profile = self.store.put("profiles", self.profile["data"], "candidate", 1)
        self.assertTrue(project(self.job, self.profile)["view"]["stale"])

    def test_queue_read_only_trash_and_cas(self):
        before = self.store.export()
        self.assertEqual(review_queue(self.store)["count"], 1)
        self.assertEqual(self.store.export(), before)
        self.save({**self.job["data"], "stage": "trash"})
        self.assertEqual(review_queue(self.store)["count"], 0)
        with self.assertRaises(Conflict):
            self.store.put("jobs", self.job["data"], "fixture", 1)

    def test_saved_graduation_window_requires_its_source(self):
        data = self.work("ineligible")
        data["graduation_windows"] = {"domestic": {"quote": "Graduation: 2027 only", "url": "https://example.invalid/campus"}}
        data["screening"]["work"]["job_hash"] = content_hash(data)
        data["screening"]["work"]["checks"][0].update(kind="mandatory", status="fail", reason="Fixture: outside graduation window", profile_fact="Fixture: 2026 graduate", jd_quote="Graduation: 2027 only", evidence_url="https://example.invalid/wrong")
        with self.assertRaises(ValueError):
            self.save(data)
        data["screening"]["work"]["checks"][0]["evidence_url"] = "https://example.invalid/campus"
        self.assertTrue(self.save(data)["view"]["low_priority"])

    def test_profile_proposal_atomic_confirmation_and_unknown_fields(self):
        proposal = self.store.put("profiles", {"base_version": 1, "patch": {"confirmed": {"major": "Changed fixture"}}, "sources": [{"url": "https://example.invalid/notion"}], "reason": "Fixture difference", "status": "pending"}, "candidate-proposal")
        with self.assertRaises(Conflict):
            confirm_proposal(self.store, 1, 99)
        self.assertEqual(self.store.get("profiles", "candidate")["version"], 1)
        result = confirm_proposal(self.store, 1, proposal["version"])
        self.assertTrue(result["data"]["confirmed"]["unknown_field"])
        self.assertEqual(result["data"]["confirmed"]["major"], "Changed fixture")
        self.assertEqual(self.store.get("profiles", "candidate-proposal")["data"]["status"], "accepted")
        with self.assertRaises(Conflict):
            confirm_proposal(self.store, 2, 2)
        client = create_app(self.store, Path(self.temp.name)).test_client()
        self.assertEqual(client.get("/api/review-queue").status_code, 200)
        self.assertEqual(client.put("/api/profile", json={"expected_version": 1, "patch": {}}, headers={"X-Workspace": "1"}).status_code, 409)
        result = client.put("/api/profile", json={"expected_version": 2, "patch": {"confirmed": {"major": "Edited fixture"}}}, headers={"X-Workspace": "1"})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.get_json()["data"]["confirmed"]["unknown_field"])


if __name__ == "__main__":
    unittest.main()
