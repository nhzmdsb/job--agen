import json
import tempfile
import unittest
from pathlib import Path

from job_agent.discover import LDC, discover, screen
from job_agent.jobs import classify, identity
from job_agent.store import Store, Conflict
from job_agent.web import create_app


class DiscoveryTests(unittest.TestCase):
    """Fictional fixtures test behavior, never real discovery acceptance."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.store = Store(self.home / "db.sqlite3")
        self.profile = self.store.put("profiles", {
            "confirmed": {"degree": "bachelor", "graduation_month": "2026-07", "education_region": "domestic", "formal_experience_years": None},
            "preferences": {"exclude_mandatory_formal_experience_from_years": 1},
            "pending": ["Unknown internship dates"]}, "candidate")

    def remote(self, calls, fail=False):
        def get(url, payload=None):
            calls.append(url)
            if "?" in url:
                offset = int(url.split("offset=")[1])
                rows = [{"id": str(n), "name": "Fictional sales trainee", "location": {"country": "cn"}} for n in (1, 2)] if offset == 0 else []
                return json.dumps({"totalFound": 101, "content": rows}).encode()
            if fail:
                raise OSError("Injected unavailable detail")
            return json.dumps({"name": "Fictional trainee", "company": {"name": "Fictional Company"},
                "postingUrl": "https://example.invalid/canonical/" + url.rsplit("/", 1)[1],
                "jobAd": {"sections": {"description": {"text": "2026年应届毕业生；本科；专业不限；应届毕业生及职场新人"}}}}).encode()
        return get

    def test_two_runs_paginate_and_take_different_jobs(self):
        calls = []
        one = discover(self.store, self.home, 1, ("ldc",), self.remote(calls))
        two = discover(self.store, self.home, 1, ("ldc",), self.remote(calls))
        self.assertEqual(len(one["new_job_ids"]), 1)
        self.assertEqual(len(two["new_job_ids"]), 1)
        self.assertNotEqual(one["new_job_ids"], two["new_job_ids"])
        self.assertEqual(calls.count(LDC + "/1"), 1)
        self.assertEqual(calls.count(LDC + "/2"), 1)
        self.assertEqual(one["scope"]["ldc"]["directory_pages"], 2)
        app = create_app(self.store, self.home)
        response = app.test_client().get("/api/jobs")
        self.assertEqual(len(response.json["items"]), 2)
        prompt = app.test_client().post("/api/jobs/" + one["new_job_ids"][0] + "/prompt", json={"task": "strategy"}, headers={"X-Workspace": "1"})
        self.assertIn("2026-07", prompt.json["prompt"])
        self.assertIn("Unknown internship dates", prompt.json["prompt"])

    def test_trash_alias_skips_without_fetch_or_restore(self):
        url = "https://example.invalid/old"
        alias = identity({"url": "https://jobs.smartrecruiters.com/LouisDreyfusCompany/1"})
        self.store.put("jobs", {"company": "Fictional Company", "title": "Old", "url": url, "stage": "trash", "dedupe_keys": [alias]}, "old")
        with self.assertRaises(Conflict):
            self.store.put("jobs", {"company": "Fictional Company", "title": "Duplicate", "url": "https://example.invalid/other", "dedupe_keys": [alias]}, "duplicate")
        calls = []
        result = discover(self.store, self.home, 1, ("ldc",), self.remote(calls))
        self.assertEqual(result["trash_skipped"], 1)
        self.assertNotIn(LDC + "/1", calls)
        self.assertEqual(self.store.get("jobs", "old")["version"], 1)

    def test_missing_detail_keeps_grey_and_retries_later(self):
        result = discover(self.store, self.home, 1, ("ldc",), self.remote([], True))
        self.assertEqual(len(result["incomplete_jd_ids"]), 1)
        record = self.store.get("jobs", result["new_job_ids"][0])
        self.assertEqual(classify(record["data"]), "grey")
        self.assertTrue(record["data"]["url"])
        second = discover(self.store, self.home, 1, ("ldc",), self.remote([], True))
        self.assertNotEqual(result["new_job_ids"], second["new_job_ids"])

    def test_evidence_not_recruitment_year_or_preferences(self):
        data = {"jd": "Bachelor or Master degree. Preferred Qualifications: 3 years of work experience", "recruitment_year": "2026"}
        result = screen(data, self.profile)
        self.assertEqual(result["qualification"], "unknown")
        self.assertEqual(result["screening"]["checks"][1]["status"], "satisfied")
        self.assertNotIn("disadvantage", [c["status"] for c in result["screening"]["checks"]])
        data = {"jd": "本科；专业不限；应届毕业生及职场新人", "graduation_windows": {"domestic": {"start": "2027-01-01", "end": "2027-06-30", "quote": "2027 graduates"}}}
        self.assertEqual(screen(data, self.profile)["qualification"], "disadvantage")
        self.assertEqual(screen({"jd": "Bachelor required; 2 years of work experience"}, self.profile)["qualification"], "disadvantage")


if __name__ == "__main__":
    unittest.main()
