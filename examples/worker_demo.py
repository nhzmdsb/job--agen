"""Fictional incremental Worker; temporary database, no network or personal data."""
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from job_agent.review import DIMENSIONS, content_hash, translation_hash
from job_agent.store import Store
from job_agent.worker import context, lookup, write


def run(store):
    counts = {"listed": 1, "details_read": 0, "reassessed": 0, "translated": 0, "skipped": 0, "failed": 0}
    identity = {"source": "fictional", "source_job_id": "demo-1", "url": "https://example.invalid/jobs/1"}
    matches = lookup(store, [identity])["items"][0]["matches"]
    if not matches:
        write(store, "jobs", {**identity, "company": "Fictional Co", "title": "Operations Associate"}, "demo")
    tasks = context(store)
    for item in tasks["items"]:
        record = item["record"]
        if "detail" in item["tasks"]:
            record = write(store, "jobs", {"jd": "Support daily operations. Bachelor degree required. No experience required."}, record["id"], record["version"], patch=True)["record"]
            counts["details_read"] += 1
        data = record["data"]
        patch = {}
        if "translation" in item["tasks"] or "detail" in item["tasks"]:
            patch["translation"] = {"source_hash": translation_hash(data), "title_zh": "虚构运营助理", "summary_zh": "协助日常运营，要求本科，无经验要求。", "jd_zh": "协助日常运营。要求本科学历。不要求工作经验。"}
            counts["translated"] += 1
        if "assessment" in item["tasks"]:
            checks = [{"dimension": d, "kind": "unknown", "status": "unknown", "reason": "虚构原文未说明", "jd_quote": "", "profile_fact": "演示未确认"} for d in DIMENSIONS]
            checks[1] = {"dimension": "education", "kind": "mandatory", "status": "pass", "reason": "虚构档案符合本科要求", "jd_quote": "Bachelor degree required.", "profile_fact": tasks["profile"]["data"]["confirmed"]["degree"]}
            patch["screening"] = {"work": {"decision": "unknown", "reason": "已读虚构 JD；届别及专业信息仍有歧义，暂列灰色。", "checks": checks, "questions": ["需要确认届别和专业要求"], "profile_version": tasks["profile"]["version"], "job_hash": content_hash(data), "assessed_at": datetime.now(timezone.utc).isoformat()}}
            counts["reassessed"] += 1
        if patch:
            write(store, "jobs", patch, record["id"], record["version"], patch=True, reason="虚构演示补必要步骤")
    if not tasks["items"]:
        counts["skipped"] = 1
    counts["remaining"] = [{"id": i["record"]["id"], "tasks": i["tasks"]} for i in context(store)["items"]]
    write(store, "runs", counts)
    return counts


def main():
    with tempfile.TemporaryDirectory(prefix="job-worker-demo-") as home:
        store = Store(Path(home) / "demo.sqlite3")
        store.put("profiles", {"confirmed": {"degree": "bachelor"}, "preferences": {}}, "candidate")
        first = run(store)
        job = store.get("jobs", "demo")
        history = store.history("jobs", "demo")
        second = run(store)
        assert second["details_read"] == second["reassessed"] == second["translated"] == 0
        assert store.get("jobs", "demo") == job and store.history("jobs", "demo") == history
        store.patch("profiles", "candidate", {"preferences": {"city": "杭州"}}, 1)
        changed = run(store)
        assert changed["details_read"] == changed["translated"] == 0 and changed["reassessed"] == 1
        print(json.dumps({"fictional_only": True, "first": first, "second": second, "conditions_changed": changed, "repeat_job_version": job["version"], "repeat_history_count": len(history)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
