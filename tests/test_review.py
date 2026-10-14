import copy
import tempfile
import unittest
from pathlib import Path

from job_agent.review import DIMENSIONS, content_hash, human_judgment_hash, project, review_queue, confirm_proposal
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
        self.assertEqual(project(self.job, self.profile)["view"]["color"], "green")
        self.assertTrue(self.save({**self.job["data"], "notes": "changed"})["view"]["fresh"])
        self.assertEqual(self.save({**self.job["data"], "interest": "yes"})["view"]["color"], "green")
        translated = {**self.job["data"], "translation": {"source_hash": content_hash(self.job["data"]), "title_zh": "虚构测试岗位", "summary_zh": "虚构测试摘要", "jd_zh": "2026 届毕业生。相关专业优先。接受实习经历。要求一年正式工作。"}}
        self.assertTrue(self.save(translated)["view"]["translation_valid"])
        self.assertEqual(self.job["data"]["jd"], self.store.history("jobs", "fixture")[0]["data"]["jd"])
        stale = self.save({**self.job["data"], "jd": self.job["data"]["jd"] + " Changed requirement."})
        self.assertTrue(stale["view"]["stale"])
        self.assertEqual(stale["view"]["color"], "green")
        self.assertFalse(stale["view"]["translation_valid"])
        self.assertFalse(stale["view"]["low_priority"])
        manual = self.save({**self.job["data"], "color": "yellow", "color_reason": "User decision"})
        self.assertEqual(manual["view"]["origin"], "人工调整")
        self.assertEqual(manual["data"]["extra"], {"keep": True})
        self.profile = self.store.put("profiles", self.profile["data"], "candidate", 1)
        self.assertTrue(project(self.job, self.profile)["view"]["stale"])

    def test_partial_translation_and_mixed_language_jd_remain_pending(self):
        data = {**self.job["data"], "title": "虚构测试岗位", "jd": "中文介绍。 Previous experience in supply chain operations required."}
        data["translation"] = {"source_hash": content_hash(data), "title_zh": "虚构测试岗位", "summary_zh": "旧摘要"}
        view = self.save(data)["view"]
        self.assertFalse(view["translation_valid"])
        self.assertTrue(view["translation_pending"])
        self.assertEqual(view["jd"], data["jd"])
        self.assertIn("translation", review_queue(self.store)["items"][0]["tasks"])
        data["translation"]["jd_zh"] = "中文介绍。要求此前具有供应链运营经验。"
        view = self.save(data)["view"]
        self.assertTrue(view["translation_valid"])
        self.assertFalse(view["translation_pending"])
        self.assertEqual(view["jd"], data["translation"]["jd_zh"])
        view = self.save({**self.job["data"], "notes": "用户备注"})["view"]
        self.assertTrue(view["translation_valid"])

    def test_queue_read_only_trash_and_cas(self):
        before = self.store.export()
        self.assertEqual(review_queue(self.store)["count"], 1)
        self.assertEqual(self.store.export(), before)
        self.save({**self.job["data"], "stage": "trash"})
        self.assertEqual(review_queue(self.store)["count"], 0)
        with self.assertRaises(Conflict):
            self.store.put("jobs", self.job["data"], "fixture", 1)

    def test_workbench_color_without_reason_is_protected_and_can_adopt_work(self):
        self.save(self.work("ineligible", "mandatory"))
        self.job = self.store.patch("jobs", "fixture", {"interest": "yes", "human_judgment": "Keep my judgment"}, self.job["version"], _human=True)
        client = create_app(self.store, Path(self.temp.name)).test_client()
        headers = {"X-Workspace": "1"}
        def edit(patch, version=None):
            return client.patch("/api/records/jobs/fixture", json={"expected_version": version or self.job["version"], "patch": patch, "human_confirm": True, "confirmation_reason": "本人编辑", "reason": "本人编辑"}, headers=headers)
        for color in ("yellow", "green"):
            response = edit({"color": color, "color_reason": ""})
            self.assertEqual(response.status_code, 200)
            self.job = response.get_json()
            self.assertEqual(self.job["view"]["color"], color)
            self.assertEqual(self.job["data"]["judgment_authorship"]["color"], "human")
            self.assertFalse(self.job["view"]["low_priority"])
            self.assertEqual(self.job["view"]["reason"], "Fixture assessment")
        version = self.job["version"]
        denied = client.patch("/api/worker/records/jobs/fixture", json={"expected_version": version, "patch": {"color": "yellow"}, "reason": "Agent attempt"}, headers=headers)
        self.assertEqual(denied.status_code, 409)
        # Worker can update its assessment without taking over the person's color.
        result = client.patch("/api/worker/records/jobs/fixture", json={"expected_version": version, "patch": {"screening": {"work": {"reason": "Updated assessment"}}}, "reason": "Recheck"}, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.job = self.store.get("jobs", "fixture")
        self.assertEqual(project(self.job, self.profile)["view"]["color"], "green")
        self.assertEqual(edit({"notes": "Stale overwrite"}, version).status_code, 409)
        result = edit({"notes": "New note", "tags": ["fixture"], "color": "", "color_reason": ""})
        self.assertEqual(result.status_code, 200)
        self.job = result.get_json()
        self.assertEqual(self.job["view"]["color"], "yellow")
        self.assertEqual(self.job["view"]["reason"], "Updated assessment")
        self.assertEqual(self.job["data"]["human_judgment"], "Keep my judgment")
        self.assertEqual(self.job["data"]["interest"], "yes")
        self.assertEqual(self.job["data"]["extra"], {"keep": True})

    def test_actual_review_prompt_contains_concise_rules_and_examples(self):
        client = create_app(self.store, Path(self.temp.name)).test_client()
        response = client.post("/api/review-prompt", json={}, headers={"X-Workspace": "1"})
        self.assertEqual(response.status_code, 200)
        prompt = response.get_json()["prompt"]
        for text in ("两三句话", "JD 未要求工作年限", "专业要求有矛盾", "医学专业只是优先", "全国出差", "空白不是待办", "不能把普通来源伪称投递入口", "Agent 不得替本人补造理由"):
            self.assertIn(text, prompt)

    def test_concise_assessment_keeps_legacy_reason_and_validates_shape(self):
        data = self.work("disadvantage")
        work = data["screening"]["work"]
        work.update(recommendation="建议暂不优先。", reasons=["虚构依据一", "虚构依据二"])
        view = self.save(data)["view"]
        self.assertEqual(view["recommendation"], work["recommendation"])
        self.assertEqual(view["reasons"], work["reasons"])
        self.assertEqual(view["reason"], "Fixture assessment")
        for reasons in ([], ["one"], ["one", ""], [1, "two"], ["a", "b", "c", "d"]):
            bad = copy.deepcopy(data)
            bad["screening"]["work"]["reasons"] = reasons
            with self.assertRaises(ValueError):
                self.save(bad)
        # Old assessments remain readable without a database rewrite.
        legacy_data = self.work()
        legacy_data["title"] = "Legacy fictional trainee"
        legacy_data.pop("dedupe_keys", None)
        legacy_data["screening"]["work"]["job_hash"] = content_hash(legacy_data)
        legacy = self.store.put("jobs", legacy_data, "legacy-assessment")
        self.assertEqual(project(legacy, self.profile)["view"]["recommendation"], "")

    def test_discard_and_undo_preserve_identity_and_check_versions(self):
        self.save(self.work("eligible"))
        client = create_app(self.store, Path(self.temp.name)).test_client()
        headers = {"X-Workspace": "1"}
        original = copy.deepcopy(self.job)
        response = client.patch("/api/records/jobs/fixture", json={"expected_version": original["version"], "patch": {"stage": "trash", "rejection_reason": ""}, "reason": "本人不考虑"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        removed = response.get_json()
        self.assertEqual(removed["id"], original["id"])
        self.assertEqual(removed["data"]["screening"], original["data"]["screening"])
        self.assertEqual(removed["data"]["notes"], original["data"]["notes"])
        self.assertEqual(review_queue(self.store)["count"], 0)
        response = client.patch("/api/records/jobs/fixture", json={"expected_version": removed["version"], "patch": {"stage": "pool", "rejection_reason": ""}, "reason": "撤销不考虑"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        restored = response.get_json()
        self.assertEqual(restored["data"]["stage"], "pool")
        self.assertEqual(len(self.store.history("jobs", "fixture")), 4)
        self.store.patch("jobs", "fixture", {"notes": "Another editor"}, restored["version"])
        response = client.patch("/api/records/jobs/fixture", json={"expected_version": restored["version"], "patch": {"stage": "trash"}, "reason": "Stale discard"}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.store.get("jobs", "fixture")["data"]["notes"], "Another editor")

    def test_feedback_is_separate_versioned_task_and_preserves_profile(self):
        self.save(self.work("eligible"))
        before = self.store.get("profiles", "candidate")
        client = create_app(self.store, Path(self.temp.name)).test_client()
        headers = {"X-Workspace": "1"}
        text = "这个岗位地点太偏，但不排除杭州。"
        result = client.patch("/api/records/jobs/fixture", json={"expected_version": self.job["version"], "patch": {"human_judgment": text}, "human_confirm": True, "confirmation_reason": "本人输入", "reason": "本人反馈"}, headers=headers)
        self.assertEqual(result.status_code, 200)
        self.job = self.store.get("jobs", "fixture")
        item = review_queue(self.store)["items"][0]
        self.assertIn("feedback", item["tasks"])
        self.assertNotIn("assessment", item["tasks"])
        self.assertEqual(item["human_judgment_hash"], human_judgment_hash(self.job["data"]))
        denied = client.patch("/api/worker/records/jobs/fixture", json={"expected_version": self.job["version"], "patch": {"human_judgment": "Agent rewrite"}, "reason": "Rewrite"}, headers=headers)
        self.assertEqual(denied.status_code, 409)
        response = client.patch("/api/worker/records/jobs/fixture", json={"expected_version": self.job["version"], "patch": {"screening": {"work": {"reason": "已收到：此岗位不优先；其他杭州岗位仍可看。", "human_judgment_hash": item["human_judgment_hash"]}}}, "reason": "回应本人反馈"}, headers=headers)
        self.assertEqual(response.status_code, 200)
        self.job = self.store.get("jobs", "fixture")
        self.assertFalse(project(self.job, self.profile)["view"]["feedback_pending"])
        self.assertEqual(self.job["data"]["human_judgment"], text)
        self.assertEqual(self.store.get("profiles", "candidate"), before)
        version = self.job["version"]
        self.job = self.store.patch("jobs", "fixture", {"human_judgment": "新看法"}, version, _human=True, human_confirm=True, confirmation_reason="本人修改", reason="本人修改")
        stale = client.patch("/api/worker/records/jobs/fixture", json={"expected_version": version, "patch": {"screening": {"work": {"human_judgment_hash": item["human_judgment_hash"]}}}, "reason": "旧回应"}, headers=headers)
        self.assertEqual(stale.status_code, 409)
        with self.assertRaises(ValueError):
            self.store.patch("jobs", "fixture", {"screening": {"work": {"reason": "旧回应"}}}, self.job["version"])
        with self.assertRaises(ValueError):
            self.store.patch("jobs", "fixture", {"human_judgment": {"bad": True}}, self.job["version"], _human=True, human_confirm=True, confirmation_reason="本人修改", reason="本人修改")

    def test_shared_company_and_recruitment_urls_do_not_merge_jobs(self):
        links = {"company_url": "https://example.invalid/company", "recruitment_url": "https://example.invalid/jobs"}
        for i in range(2):
            self.store.put("jobs", {"company": "Shared fixture", "title": f"Role {i}", "detail_url": f"https://example.invalid/#/detail?JobID={i}", **links}, f"shared-{i}")
        self.assertNotEqual(self.store.get("jobs", "shared-0")["id"], self.store.get("jobs", "shared-1")["id"])
        self.assertEqual(self.store.get("jobs", "shared-1")["data"]["detail_url"], "https://example.invalid/#/detail?JobID=1")
        for field in ("application_url", "company_url", "recruitment_url"):
            with self.assertRaises(ValueError):
                self.store.patch("jobs", "shared-0", {field: "javascript:bad"}, 1)

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
