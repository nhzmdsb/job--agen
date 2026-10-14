"""Small shared rules for structured workbench jobs; legacy envelopes remain readable."""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    return "green"


def normalized_query(query):
    # Only established tracking parameters; requisition IDs and unknown keys survive.
    return urlencode(sorted((k, v) for k, v in parse_qsl(query, keep_blank_values=True) if not k.lower().startswith("utm_") and k.lower() not in ("gclid", "fbclid", "msclkid")))


def normalized_url(url):
    parts = urlsplit(url.strip())
    fragment = parts.fragment
    if "?" in fragment:
        route, query = fragment.split("?", 1)
        fragment = route + "?" + normalized_query(query)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), normalized_query(parts.query), fragment))


def identity(data):
    for key in ("company", "title", "url", "detail_url"):
        if data.get(key) is not None and not isinstance(data[key], str):
            raise ValueError("公司、岗位与来源链接必须是文字")
    url = normalized_url(data.get("url") or data.get("detail_url") or "")
    # Prefer source requisition ID, then URL; title changes do not create a new job.
    source = data.get("source") or data.get("discovery_source")
    key = ["source", source, str(data["source_job_id"])] if source and data.get("source_job_id") else ["url", url] if url else ["job", data.get("company", "").strip().casefold(), data.get("title", "").strip().casefold(), data.get("source_job_id", "")]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def identity_keys(data):
    keys, obsolete = set(), set()
    for entry in [data, *data.get("identity_aliases", [])]:
        if not isinstance(entry, dict):
            raise ValueError("identity_aliases must contain objects")
        if (entry.get("source") or entry.get("discovery_source")) and entry.get("source_job_id"):
            keys.add(identity(entry))
        for field in ("url", "detail_url", "boss_url"):
            if entry.get(field):
                keys.add(identity({"url": entry[field]}))
                parts = urlsplit(entry[field])
                if parts.fragment:
                    # Old code erased fragments. Ignore that lossy key on read,
                    # unless the base URL is also an explicitly saved alias.
                    obsolete.add(identity({"url": urlunsplit(parts._replace(fragment=""))}))
    keys |= set(data.get("dedupe_keys", [])) - obsolete
    if not keys and data.get("company"):
        keys.add(identity(data))
    return keys


def prepare(db, data, old, record_id, options, conflict_type):
    data = dict(data)
    if any(not isinstance(data.get(key), str) or not data[key].strip() for key in ("company", "title")):
        raise ValueError("请填写公司与岗位名称，招聘入口也可以作为线索保存")
    if data.get("stage", "pool") not in STAGES:
        raise ValueError("未知进度")
    for key in ("url", "detail_url", "boss_url", "application_url", "company_url", "recruitment_url"):
        if data.get(key) and (not isinstance(data[key], str) or urlsplit(data[key]).scheme not in ("http", "https")):
            raise ValueError("来源链接必须以 http:// 或 https:// 开头")
    if data.get("qualification", "unknown") not in ("unknown", "eligible", "disadvantage"):
        raise ValueError("未知资格状态")
    records = [(r["id"], json.loads(r["data"])) for r in db.execute("SELECT id,data FROM records WHERE collection='jobs'")]
    key = identity(data)
    aliases = identity_keys(data)
    for other_id, other in records:
        if other_id != record_id and other.get("company") and not other.get("merged_into") == record_id and not data.get("merged_into") == other_id and aliases.intersection(identity_keys(other)):
            raise conflict_type("此岗位已保存（包括垃圾桶），不会再次推荐")
    previous = old["data"] if old else {}
    if data.get("human_judgment") != previous.get("human_judgment") and not isinstance(data.get("human_judgment", ""), str):
        raise ValueError("本人判断必须是文字")
    work = data.get("screening", {}).get("work")
    if work != previous.get("screening", {}).get("work"):
        from .review import validate_assessment
        validate_assessment(data)
        if work:
            profile = db.execute("SELECT version,data FROM records WHERE collection='profiles' AND id='candidate'").fetchone()
            if not profile or profile[0] != work["profile_version"]:
                raise conflict_type("个人档案已变化，请重新读取后判断")
            from .review import profile_hash
            work["profile_hash"] = profile_hash(json.loads(profile["data"]))
            work["policy_version"] = 1
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
    data["dedupe_keys"] = sorted(identity_keys(previous) | aliases | {key})
    data["display_color"] = classify(data)
    return data
