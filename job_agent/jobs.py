"""Small shared rules for structured workbench jobs; legacy envelopes remain readable."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

STAGES = ("pool", "preparing", "applied", "interview", "ended", "trash")


def validate_quotas(data):
    if not isinstance(data.get("items"), list):
        raise ValueError("企业名额必须是列表")
    seen = set()
    for q in data["items"]:
        if not isinstance(q, dict):
            raise ValueError("企业名额每项必须包含企业、批次与名额")
        if not isinstance(q.get("company"), str) or not q["company"].strip() or type(q.get("limit")) is not int or q["limit"] < 1 or not isinstance(q.get("cycle"), str) or not q["cycle"].strip():
            raise ValueError("企业名额必须是正整数，并填写企业与批次")
        key = (q["company"].strip().casefold(), q["cycle"])
        if key in seen:
            raise ValueError("同一企业与批次不能设置重复名额")
        seen.add(key)


def classify(data):
    if not data.get("jd") or data.get("qualification", "unknown") == "unknown":
        return "grey"
    if data.get("qualification") == "disadvantage":
        return "yellow"
    if data.get("color_reason") and data.get("color") in ("green", "yellow", "beige", "grey"):
        return data["color"]
    return "green" if data.get("interest") == "yes" else "beige"


def identity(data):
    for key in ("company", "title", "url"):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise ValueError("公司、岗位与来源链接必须是文字")
    parts = urlsplit((data.get("url") or "").strip())
    url = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, ""))
    # A stable source URL wins over title changes, including records in the trash.
    key = ["url", url] if url else ["job", data.get("company", "").strip().casefold(), data.get("title", "").strip().casefold(), data.get("source_job_id", "")]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def prepare(db, data, old, record_id, options, conflict_type):
    data = dict(data)
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in ("company", "title")):
        raise ValueError("请填写公司与岗位名称，招聘入口也可以作为线索保存")
    if data.get("stage", "pool") not in STAGES:
        raise ValueError("未知进度")
    for key in ("url", "boss_url"):
        if data.get(key) and (not isinstance(data[key], str) or urlsplit(data[key]).scheme not in ("http", "https")):
            raise ValueError("来源链接必须以 http:// 或 https:// 开头")
    if data.get("qualification", "unknown") not in ("unknown", "eligible", "disadvantage"):
        raise ValueError("未知资格状态")
    records = [(r["id"], json.loads(r["data"])) for r in db.execute("SELECT id,data FROM records WHERE collection='jobs'")]
    key = identity(data)
    for other_id, other in records:
        if other_id != record_id and other.get("company") and (key == identity(other) or key in other.get("dedupe_keys", [])):
            raise conflict_type("此岗位已保存（包括垃圾桶），不会再次推荐")
    previous = old["data"] if old else {}
    if data.get("stage") == "applied" and previous.get("stage") != "applied":
        company, cycle = data["company"].strip().casefold(), data.get("cycle", "未指定批次")
        row = db.execute("SELECT data FROM records WHERE collection='profiles' AND id='company-quotas'").fetchone()
        quotas = json.loads(row[0]).get("items", []) if row else []
        quota = next((q for q in quotas if q["company"].strip().casefold() == company and q["cycle"] == cycle), None)
        if quota:
            count = sum(1 for other_id, d in records if other_id != record_id and d.get("company", "").strip().casefold() == company and d.get("cycle", "未指定批次") == cycle and (d.get("stage") in ("applied", "interview") or any(h.get("stage") == "applied" for h in d.get("application_history", []))))
            if count >= quota["limit"]:
                if not options.get("quota_confirm") or not isinstance(options.get("quota_reason"), str) or not options["quota_reason"].strip():
                    raise conflict_type(f"quota:{count}/{quota['limit']}：同企业同批次名额已用满，明确确认并填写原因后才能记录已投递")
                data["quota_override_reason"] = options["quota_reason"].strip()
    if previous.get("stage", "pool") != data.get("stage", "pool"):
        data["application_history"] = previous.get("application_history", []) + [{"stage": data.get("stage", "pool"), "at": datetime.now(timezone.utc).isoformat(), "reason": options.get("reason", "用户更新进度")}]
    data["dedupe_key"] = key
    data["dedupe_keys"] = list(dict.fromkeys(previous.get("dedupe_keys", []) + ([identity(previous)] if previous else []) + [key]))
    data["display_color"] = classify(data)
    return data
