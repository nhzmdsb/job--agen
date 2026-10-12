import json
import tempfile
import unittest
from unittest.mock import patch
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

    def test_completed_second_run_reuses_results_and_scans_reordered_list(self):
        from test_worker import assessment
        from job_agent.review import translation_hash
        discover(self.store, self.home, 2, ("ldc",), self.remote([]))
        for record in self.store.records("jobs"):
            self.store.patch("jobs", record["id"], {
                "screening": {"work": assessment(record["data"], self.profile)},
                "translation": {"source_hash": translation_hash(record["data"]),
                                "title_zh": "虚构岗位", "summary_zh": "虚构摘要", "jd_zh": record["data"]["jd"]}}, record["version"])
        before = {r["id"]: self.store.pack("jobs", r["id"]) for r in self.store.records("jobs")}
        calls = []
        second = discover(self.store, self.home, 2, ("ldc",), self.remote(calls))
        self.assertEqual(second["details_attempted"], 0)
        self.assertEqual(second["remaining"], [])
        self.assertEqual(second["status"], "completed")
        for key, pack in before.items():
            self.assertEqual(self.store.pack("jobs", key), pack)
        remote = self.remote(calls)
        def reordered(url, payload=None):
            if "?" in url and "offset=0" in url:
                return json.dumps({"totalFound": 3, "content": [
                    {"id": str(n), "name": "Fictional sales trainee", "location": {"country": "cn"}} for n in (2, 3, 1)]}).encode()
            return remote(url, payload)
        third = discover(self.store, self.home, 2, ("ldc",), reordered)
        self.assertEqual(third["details_read"], 1)
        self.assertEqual(len(self.store.records("jobs")), 3)
        for key, pack in before.items():
            self.assertEqual(self.store.pack("jobs", key), pack)

    def test_refresh_failure_preserves_results_and_explicit_retry(self):
        discover(self.store, self.home, 2, ("ldc",), self.remote([]))
        before = self.store.records("jobs")
        succeeded = {r["id"]: r["data"]["detail_succeeded_at"] for r in self.store.records("leads")}
        failed = discover(self.store, self.home, 2, ("ldc",), self.remote([], True), refresh=True)
        self.assertEqual(failed["failed"], 2)
        self.assertEqual(self.store.records("jobs"), before)
        for lead in self.store.records("leads"):
            self.assertEqual(lead["data"]["detail_succeeded_at"], succeeded[lead["id"]])
            self.assertTrue(lead["data"]["detail_attempted_at"])
            self.assertTrue(lead["data"]["retry_after"])
        backed_off = discover(self.store, self.home, 2, ("ldc",), self.remote([], True), refresh=True)
        self.assertEqual(backed_off["details_attempted"], 0)
        retried = discover(self.store, self.home, 2, ("ldc",), self.remote([]), refresh=True, retry=True)
        self.assertEqual(retried["details_read"], 2)
        self.assertTrue(all(not r["data"]["detail_error"] for r in self.store.records("leads")))

    def test_interruption_leaves_resumable_run(self):
        remote = self.remote([])
        def interrupted(url, payload=None):
            if url == LDC + "/2":
                raise KeyboardInterrupt()
            return remote(url, payload)
        with self.assertRaises(KeyboardInterrupt):
            discover(self.store, self.home, 2, ("ldc",), interrupted)
        run = self.store.records("runs")[0]["data"]
        self.assertEqual(run["status"], "running")
        self.assertEqual(run["details_read"], 1)
        self.assertTrue(any("assessment" in r["tasks"] for r in run["remaining"]))
        self.assertTrue(any(r.get("lead_id") for r in run["remaining"]))
        calls = []
        resumed = discover(self.store, self.home, 2, ("ldc",), self.remote(calls))
        self.assertEqual(resumed["details_read"], 1)
        self.assertNotIn(LDC + "/1", calls)
        self.assertIn(LDC + "/2", calls)

    def test_empty_refresh_is_failure_and_keeps_cached_jd(self):
        discover(self.store, self.home, 2, ("ldc",), self.remote([]))
        before = self.store.records("jobs")
        with patch('job_agent.discover.detail', return_value={'company': 'Fictional', 'title': 'Empty', 'jd': ''}):
            result = discover(self.store, self.home, 2, ("ldc",), self.remote([]), refresh=True)
        self.assertEqual(result['failed'], 2)
        self.assertEqual(result['details_read'], 0)
        self.assertEqual(self.store.records('jobs'), before)
        self.assertTrue(all('完整 JD' in lead['data']['detail_error'] for lead in self.store.records('leads')))


if __name__ == "__main__":
    unittest.main()
