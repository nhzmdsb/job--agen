"""Small official-page collector; no login, model, or application submission."""
import argparse
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from .jobs import identity
from .store import Store, private_home

DEFAULT_URL = "https://canonical.com/careers/8249005/graduate-software-engineer-open-source-and-linux-canonical-ubuntu-remote"


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        if data.strip():
            self.parts.append(data.strip())


def text(html):
    parser = Text()
    parser.feed(html)
    return "\n".join(parser.parts)


def extract(html, url):
    for script in re.findall(r'<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            value = json.loads(script)
        except ValueError:
            continue
        objects = value if isinstance(value, list) else value.get("@graph", [value]) if isinstance(value, dict) else []
        for obj in objects:
            if isinstance(obj, dict) and obj.get("@type") == "JobPosting":
                organization = obj.get("hiringOrganization", {})
                organization = organization if isinstance(organization, dict) else {}
                return {"company": organization.get("name", "未确认企业"), "title": obj.get("title", "招聘线索"), "jd": text(obj.get("description", ""))}
    if url == DEFAULT_URL:
        page = text(html)
        match = re.search(r"We are hiring\s+2025 and 2026", page)
        start = match.start() if match else -1
        if start >= 0:
            end = page.find("Apply", start)
            return {"company": "Canonical", "title": "Graduate Software Engineer, Open Source and Linux", "jd": page[start:end if end >= 0 else None]}
        return {"company": "Canonical", "title": "Graduate Software Engineer, Open Source and Linux", "jd": ""}
    raise ValueError("网页未提供可识别的 JobPosting；请在工作台保留原始入口并手动补充资料")


def collect(url=DEFAULT_URL, store=None, home=None):
    if not url.startswith("https://"):
        raise ValueError("采集来源必须使用 HTTPS")
    store, home = store or Store(), home or private_home()
    request = urllib.request.Request(url, headers={"User-Agent": "JobAgent-V0/official-page"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("来源网页超过 4 MB；保留入口，停止本次读取")
        html = raw.decode(response.headers.get_content_charset() or "utf-8", errors="replace")
    found = extract(html, url)
    now = datetime.now(timezone.utc).isoformat()
    key = identity({**found, "url": url})
    existing = None
    offset = 0
    while True:
        page = store.list("jobs", 200, offset)
        existing = next((r for r in page["items"] if key in r["data"].get("dedupe_keys", []) or identity(r["data"]) == key), None)
        if existing or page["next_offset"] is None:
            break
        offset = page["next_offset"]
    source_id = "official-" + hashlib.sha256(url.encode()).hexdigest()
    try:
        source = store.get("sources", source_id)
    except KeyError:
        source = {"version": 0, "data": {}}
    store.put("sources", {**source["data"], "url": url, "last_checked_at": now, "status": "jd_read" if found["jd"] else "entrance_only"}, source_id, source["version"], "collector", "读取官方招聘页面")
    if existing and (existing["data"].get("stage") == "trash" or existing["data"].get("jd") == found["jd"]):
        return {"new": False, "updated": False, "id": existing["id"], "status": "trash_retained" if existing["data"].get("stage") == "trash" else "unchanged"}
    sha = hashlib.sha256(raw).hexdigest()
    folder = home / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / sha).write_bytes(raw)
    artifact_id = "official-source-" + sha
    try:
        store.get("artifacts", artifact_id)
    except KeyError:
        store.put("artifacts", {"name": "official-recruitment-source.html", "kind": "source_evidence", "sha256": sha, "url": url, "accessed_at": now}, artifact_id, actor="collector", reason="保存官方网页原始证据；不是简历")
    data = {"qualification": "unknown", "interest": "unknown", "stage": "pool", **(existing["data"] if existing else {}), **found, "url": url, "accessed_at": now}
    data["artifacts"] = list(dict.fromkeys([*data.get("artifacts", []), artifact_id]))
    if url == DEFAULT_URL and "2025 and 2026" in found["jd"]:
        data.update(eligible_years="2025、2026", target_cohort="2025、2026", qualification_evidence="官网原文：We are hiring 2025 and 2026；访问时间：" + now)
    record = store.put("jobs", data, existing["id"] if existing else "job-" + key, existing["version"] if existing else 0, "collector", "官方来源与完整 JD 入库；个人硬性资格仍需核对")
    return {"new": existing is None, "updated": existing is not None, "id": record["id"], "jd_characters": len(found["jd"])}


def main():
    parser = argparse.ArgumentParser(description="读取可访问的官方招聘页面并保留来源证据")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    args = parser.parse_args()
    print(json.dumps(collect(args.url), ensure_ascii=False))


if __name__ == "__main__":
    main()
