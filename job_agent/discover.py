"""Discover official vacancies before reading details. No applications or scoring."""
import argparse
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone, timedelta
from urllib.parse import urljoin

from .collect import extract, text
from .jobs import identity, identity_keys
from .store import Store, private_home
from .sync import process_lock

LDC = "https://api.smartrecruiters.com/v1/companies/LouisDreyfusCompany/postings"
IFF = "https://iff.wd5.myworkdayjobs.com/wday/cxs/iff/IFF_Careers"
ZTC = "https://www.ztccloud.com.cn/careers/campus-jobs.html"
CAMPAIGN = "https://www.ztccloud.com.cn/careers/campus.html"


def visible(html):
    return text(re.sub(r"<(script|style)\b[^>]*>.*?</\1>", "", html, flags=re.S | re.I))


def fetch(url, payload=None):
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                     headers={"User-Agent": "Mozilla/5.0 JobAgent/official-discovery", "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("来源超过 4 MB，保留入口")
        return raw


def objects(html):
    result = []
    for script in re.findall(r'<script\b[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            value = json.loads(script)
        except ValueError:
            continue
        result.extend(value if isinstance(value, list) else value.get("@graph", [value]) if isinstance(value, dict) else [])
    return [o for o in result if isinstance(o, dict)]


def enumerate_source(source, get):
    """Return (links, raw pages); all directory pages precede detail requests."""
    links, pages = [], []
    if source == "ldc":
        offset = 0
        while True:
            url = f"{LDC}?limit=100&offset={offset}"
            raw = get(url)
            data = json.loads(raw)
            pages.append((url, raw))
            rows = data.get("content", [])
            for row in rows:
                if row.get("location", {}).get("country") == "cn":
                    links.append({"source": source, "company": "Louis Dreyfus Company", "title": row["name"],
                                  "url": "https://jobs.smartrecruiters.com/LouisDreyfusCompany/" + str(row["id"]),
                                  "detail_url": LDC + "/" + str(row["id"]), "source_job_id": str(row["id"])})
            offset += len(rows)
            if not rows or offset >= data["totalFound"]:
                break
    elif source == "iff":
        offset = 0
        while True:
            url = IFF + "/jobs"
            raw = get(url, {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": "China"})
            data = json.loads(raw)
            pages.append((url + "?offset=" + str(offset) + "&search=China", raw))
            rows = data.get("jobPostings", [])
            for row in rows:
                links.append({"source": source, "company": "IFF", "title": row["title"],
                              "url": "https://iff.wd5.myworkdayjobs.com/IFF_Careers" + row["externalPath"],
                              "detail_url": IFF + row["externalPath"], "source_job_id": row["externalPath"]})
            # Workday may return total=0 on the last page despite returning a row.
            offset += len(rows)
            if len(rows) < 20:
                break
    else:
        raw = get(ZTC)
        pages.append((ZTC, raw))
        for item_list in objects(raw.decode("utf-8")):
            if item_list.get("@type") == "ItemList":
                for row in item_list.get("itemListElement", []):
                    item = row.get("item", row)
                    url = urljoin(ZTC, item.get("url", ""))
                    if url.startswith("https://www.ztccloud.com.cn/careers/campus-"):
                        links.append({"source": source, "company": "中兴新云", "title": item.get("name", "招聘线索"),
                                      "url": url, "detail_url": url, "source_job_id": url})
        campaign = get(CAMPAIGN)
        pages.append((CAMPAIGN, campaign))
        body = visible(campaign.decode("utf-8"))
        windows = {}
        for category, label in (("domestic", "国内毕业生"), ("overseas", "海外留学生")):
            match = re.search(label + r"\s*(\d{4}/\d{2}/\d{2})\s*[–—-]\s*(\d{4}/\d{2}/\d{2})", body)
            if match:
                windows[category] = {"start": match[1].replace("/", "-"), "end": match[2].replace("/", "-"), "quote": match[0], "url": CAMPAIGN}
        for link in links:
            link["graduation_windows"] = windows
    return list({r["detail_url"]: r for r in links}.values()), pages


def detail(link, raw):
    source = link["source"]
    if source == "ldc":
        obj = json.loads(raw)
        sections = obj["jobAd"]["sections"]
        jd = "\n\n".join(visible(s.get("text", "")) for s in sections.values())
        custom = {s.get("fieldLabel"): s.get("valueLabel") for s in obj.get("customField", [])}
        result = {"company": obj["company"]["name"], "title": obj["name"], "jd": jd,
                  "city": obj.get("location", {}).get("city", ""), "url": obj.get("postingUrl", link["url"]),
                  "education": custom.get("Required Education", ""), "experience": custom.get("Years of Work Experience", ""),
                  "source_fields": custom, "active": obj.get("active"), "published_at": obj.get("releasedDate", "")}
    elif source == "iff":
        obj = json.loads(raw)
        info = obj["jobPostingInfo"]
        result = {"company": obj.get("hiringOrganization", {}).get("name", "IFF"), "title": info["title"],
                  "jd": visible(info["jobDescription"]), "city": info.get("location", ""),
                  "url": info.get("externalUrl", link["url"]), "active": info.get("canApply"),
                  "published_at": info.get("startDate", ""), "source_job_id": info.get("jobReqId", link["source_job_id"])}
    else:
        html = raw.decode("utf-8")
        obj = next(o for o in objects(html) if o.get("@type") == "JobPosting")
        locations = obj.get("jobLocation", [])
        locations = locations if isinstance(locations, list) else [locations]
        result = {**extract(html, link["url"]), "url": link["url"],
                  "city": "、".join(l.get("address", {}).get("addressLocality", "") for l in locations),
                  "published_at": obj.get("datePosted", ""), "valid_through": obj.get("validThrough", ""),
                  "graduation_windows": link.get("graduation_windows", {})}
    if not result["jd"].strip():
        raise ValueError("详情未提供 JD 正文")
    result["recruitment_year"] = result.get("published_at", "")[:4]
    return result


def screen(data, profile):
    """Only explicit hard evidence excludes eligibility; missing evidence stays unknown."""
    checks, questions = [], []
    jd = data.get("jd", "")
    if not profile:
        return {"qualification": "unknown", "qualification_evidence": "未取得已确认个人档案", "screening": {"questions": ["确认个人档案"]}}
    candidate = profile["data"]
    facts = candidate.get("confirmed", {})
    def check(field, status, evidence):
        checks.append({"field": field, "status": status, "evidence": evidence})
        if status == "unknown":
            questions.append(evidence)
    windows = data.get("graduation_windows", {})
    window = windows.get(facts.get("education_region"))
    graduation = facts.get("graduation_month", "")
    if window and graduation:
        data["eligible_years"] = window["start"] + " 至 " + window["end"] + "（" + facts["education_region"] + "）"
        data["target_cohort"] = window["end"][:4] + "（毕业日期窗口，见原文）"
        # Month precision: a boundary inside that month needs confirmation.
        month_start = graduation + "-01"
        if graduation < window["start"][:7] or graduation > window["end"][:7]:
            status = "disadvantage"
        elif month_start >= window["start"] and graduation < window["end"][:7]:
            status = "satisfied"
        else:
            status = "unknown"
        check("graduation", status, window["quote"] + "；个人毕业月份：" + graduation)
    else:
        match = re.search(r"20\d{2}年?\s*应届毕业生", jd)
        if match and graduation:
            year = match[0][:4]
            check("graduation", "satisfied" if graduation.startswith(year) else "unknown", match[0])
            data["eligible_years"] = year + "（原文明确应届；其他毕业年待确认）"
            data["target_cohort"] = year
        else:
            check("graduation", "unknown", "JD 未明确允许申请的毕业年份，不能把招聘活动年份当成毕业资格")
    degree = re.search(r"(?:硕士|博士|本科)[^。；\n]{0,30}|Bachelor[^.\n]{0,100}|Master[^.\n]{0,100}", jd, re.I)
    if degree:
        quote = degree[0]
        if re.search(r"优先|preferred", quote, re.I):
            check("education", "unknown", quote + "（优先条件，硬性学历仍待确认）")
        else:
            higher = re.search(r"硕士|博士|Master", quote, re.I) and not re.search(r"本科|Bachelor", quote, re.I)
            check("education", "disadvantage" if higher and facts.get("degree") == "bachelor" else "satisfied" if facts.get("degree") == "bachelor" else "unknown", quote)
        data["education"] = quote
    else:
        check("education", "unknown", "学历要求未明确")
    major = re.search(r"专业不限|不限专业|any (?:academic )?major", jd, re.I)
    check("major", "satisfied" if major else "unknown", major[0] if major else "专业要求需核对；相关学科/参考专业不等于明确接受当前专业")
    # Remove clearly labelled preferences before checking mandatory experience.
    required = re.split(r"How Would You Stand Out\?|Preferred Qualifications", jd, flags=re.I)[0]
    minimum = re.search(r"(\d+)\s*\+?\s*(?:years? of (?:relevant )?(?:work )?experience|年以上[^。\n]{0,12}经验)", required, re.I)
    graduate_alternative = bool(re.search(r"应届毕业生及职场新人|graduates?\s+(?:or|and)", required, re.I))
    if minimum and not graduate_alternative:
        years = facts.get("formal_experience_years")
        formal = bool(re.search(r"work|professional|工作", minimum[0], re.I))
        policy = candidate.get("preferences", {}).get("exclude_mandatory_formal_experience_from_years")
        excluded = formal and policy is not None and int(minimum[1]) >= policy
        check("experience", "disadvantage" if excluded or years is not None and years < int(minimum[1]) else "unknown",
              minimum[0] + ("；用户明确不考虑此正式工作经验门槛，记录保留" if excluded else "；需确认是否为正式工作经验"))
        data["experience"] = minimum[0]
    elif graduate_alternative or data.get("experience") == "0+":
        check("experience", "satisfied", "原文提供应届/零经验入口；不把其他申请人路径的经验当成必需")
    else:
        check("experience", "unknown", "未找到明确硬性经验门槛；不得把实习或优先项当成必需")
    if re.search(r"熟练英文|Strong[^\n]{0,70}English|fluent[^\n]{0,30}English", jd, re.I):
        questions.append("岗位有英语要求，个人自述不等于实际能力测评，请人工确认")
    if data.get("active") is False or data.get("valid_through", "") and data["valid_through"][:10] < datetime.now(timezone.utc).date().isoformat():
        check("availability", "disadvantage", "官方详情已关闭申请或截止日期已过；保留来源，不自动删除")
    status = "disadvantage" if any(c["status"] == "disadvantage" for c in checks) else "unknown" if questions or any(c["status"] == "unknown" for c in checks) or not jd else "eligible"
    return {"qualification": status, "qualification_evidence": "\n".join(c["field"] + "：" + c["status"] + "；" + c["evidence"] for c in checks),
            "screening": {"profile_id": profile["id"], "profile_version": profile["version"], "checks": checks, "questions": questions,
                          "responsibilities_excerpt": re.split(r"任职要求|任职资格|Qualifications|What you will need", jd, flags=re.I)[0][:2000],
                          "candidate_background": facts.get("experience", []),
                          "responsibilities_review": "并列保留职责原文与已确认经历供讨论；没有明确资格证据时不因职责领域不同排除，不作评分"}}


def save(store, collection, record_id, data, reason):
    try:
        old = store.get(collection, record_id)
    except KeyError:
        old = {"version": 0, "data": {}}
    merged = {**old["data"], **data}
    if merged == old["data"]:
        return old
    return store.put(collection, merged, record_id, old["version"], "discovery", reason)


def evidence(store, home, url, raw, now):
    sha = hashlib.sha256(raw).hexdigest()
    folder = home / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / sha).write_bytes(raw)
    record_id = "source-" + sha
    try:
        store.get("artifacts", record_id)
    except KeyError:
        store.put("artifacts", {"name": "official-source.txt", "kind": "source_evidence", "sha256": sha, "size": len(raw), "url": url, "accessed_at": now}, record_id, actor="discovery", reason="保留官方原始响应，非简历")
    return record_id


def priority(link):
    return 0 if re.search(r"trainee|sales|commercial|procurement|logistics|marketing|product|销售|售前|产品|咨询|实施", link["title"], re.I) else 1


def discover(store=None, home=None, batch=4, sources=("ldc", "ztc", "iff"), get=fetch, refresh=False, retry=False):
    store, home = store or Store(), home or private_home()
    with process_lock(home / "discovery.lock"):
        now = datetime.now(timezone.utc).isoformat()
        try:
            profile = store.get("profiles", "candidate")
        except KeyError:
            profile = None
        jobs = [r for r in store.records() if r["collection"] == "jobs"]
        known = {key: r for r in jobs for key in identity_keys(r["data"])}
        result = {"started_at": now, "scope": {}, "new_job_ids": [], "incomplete_jd_ids": [], "errors": [], "trash_skipped": 0, "details_attempted": 0, "details_read": 0, "list_discovered": 0, "skipped": 0, "reassessed": 0, "failed": 0, "status": "running"}
        run = store.put("runs", result, actor="discovery", reason="开始增量扫描")
        def checkpoint():
            nonlocal run
            from .review import review_queue
            remaining, offset = [], 0
            while True:
                page = review_queue(store, 200, offset)
                remaining.extend({"job_id": item["record"]["id"], "tasks": item["tasks"]} for item in page["items"])
                if page["next_offset"] is None:
                    break
                offset = page["next_offset"]
            for lead in store.records("leads"):
                entry = lead["data"]
                if entry.get("status") == "skipped" or not entry.get("detail_url"):
                    continue
                old = next((known[k] for k in identity_keys(entry) if k in known), None)
                if old and (old["data"].get("merged_into") or old["data"].get("stage", "pool") not in ("pool", "preparing")):
                    continue
                if not old or not old["data"].get("jd") or entry.get("detail_error"):
                    remaining.append({"lead_id": lead["id"], "tasks": ["detail"], "retry_after": entry.get("retry_after", "")})
            result["remaining"] = remaining
            run = store.put("runs", result, run["id"], run["version"], actor="discovery", reason="记录可续做进度")
        for source in sources:
            try:
                links, pages = enumerate_source(source, get)
                artifacts = [evidence(store, home, url, raw, now) for url, raw in pages]
                # Save the directory's new links before choosing the next details.
                leads = []
                for link in sorted(links, key=priority):
                    lead_id = "discovered-" + identity({"url": link["detail_url"]})
                    lead = save(store, "leads", lead_id, {**link, "accessed_at": now, "artifacts": artifacts}, "官方目录发现岗位链接")
                    leads.append(lead)
                result["list_discovered"] += len(links)
                result["scope"][source] = {"listed": len(links), "directory_pages": len(pages), "details_attempted": 0, "waiting": 0,
                                           "coverage": "China search results" if source == "iff" else "China directory" if source == "ldc" else "campus directory"}
                checkpoint()
                attempted, waiting = 0, 0
                for lead in leads:
                    link = lead["data"]
                    if link.get("status") == "skipped":
                        result["skipped"] += 1
                        continue
                    old = next((known[k] for k in identity_keys(link) if k in known), None)
                    if not old and link.get("job_id"):
                        try:
                            old = store.get("jobs", link["job_id"])
                        except KeyError:
                            pass
                    if old and old["data"].get("stage") == "trash":
                        result["trash_skipped"] += 1
                        continue
                    if old and (old["data"].get("merged_into") or old["data"].get("stage", "pool") not in ("pool", "preparing")):
                        result["skipped"] += 1
                        continue
                    if old and old["data"].get("jd") and not refresh:
                        result["skipped"] += 1
                        continue
                    if not retry and link.get("retry_after", "") > now:
                        continue
                    if attempted >= batch:
                        waiting += 1
                        continue
                    attempted += 1
                    result["details_attempted"] += 1
                    result["scope"][source]["details_attempted"] = attempted
                    save(store, "leads", lead["id"], {"detail_attempted_at": now}, "开始读取详情")
                    checkpoint()
                    found = {"company": link["company"], "title": link["title"], "url": link["url"], "jd": ""}
                    ids = list(artifacts)
                    failure = None
                    try:
                        raw = get(link["detail_url"])
                        ids.append(evidence(store, home, link["detail_url"], raw, now))
                        found = detail(link, raw)
                        if not isinstance(found.get("jd"), str) or not found["jd"].strip():
                            raise ValueError("详情未取得完整 JD")
                        result["details_read"] += 1
                    except (OSError, ValueError, KeyError, StopIteration) as exc:
                        failure = type(exc).__name__ + ": " + str(exc)
                        result["errors"].append({"url": link["detail_url"], "error": failure})
                        result["failed"] += 1
                    # Directory links and canonical detail URLs can identify the same saved job.
                    old = old or known.get(identity(found))
                    if old and (old["data"].get("merged_into") or old["data"].get("stage", "pool") not in ("pool", "preparing") or (old["data"].get("jd") and not refresh)):
                        save(store, "leads", lead["id"], {"job_id": old["id"], "detail_checked_at": now}, "详情链接归并到既有岗位，不恢复垃圾桶")
                        if old["data"].get("stage") == "trash":
                            result["trash_skipped"] += 1
                        continue
                    if failure and old and old["data"].get("jd"):
                        save(store, "leads", lead["id"], {"job_id": old["id"], "detail_attempted_at": now, "detail_error": failure, "retry_after": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()}, "刷新失败，保留已有结果")
                        checkpoint()
                        continue
                    data = {"stage": "pool", "interest": "unknown", **(old["data"] if old else {}), **found, "accessed_at": now,
                            "discovery_source": source, "source_job_id": found.get("source_job_id", link["source_job_id"]), "source_listing": pages[0][0],
                            "artifacts": list(dict.fromkeys([*(old["data"].get("artifacts", []) if old else []), *ids]))}
                    data["dedupe_keys"] = list(dict.fromkeys([*(data.get("dedupe_keys", [])), identity(link), identity(found)]))
                    screened = screen(data, profile)
                    if not old or old["data"].get("qualification_origin") == "discovery":
                        screened["screening"] = {**data.get("screening", {}), **screened.get("screening", {})}
                        data.update(screened, qualification_origin="discovery")
                    record_id = old["id"] if old else "job-" + identity(found)
                    job = store.put("jobs", data, record_id, old["version"] if old else 0, "discovery", "官方新链接 → JD → 证据初筛；不自动投递")
                    for key in job["data"]["dedupe_keys"]:
                        known[key] = job
                    save(store, "leads", lead["id"], {"job_id": job["id"], "detail_attempted_at": now, "detail_checked_at": now, **({"detail_succeeded_at": now} if not failure else {}), "detail_error": failure,
                         "retry_after": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat() if failure else ""}, "保存实际详情处理进度")
                    if not old:
                        result["new_job_ids"].append(job["id"])
                    if not found["jd"]:
                        result["incomplete_jd_ids"].append(job["id"])
                    checkpoint()
                result["scope"][source] = {"listed": len(links), "directory_pages": len(pages), "details_attempted": attempted, "waiting": waiting,
                                           "coverage": "China search results" if source == "iff" else "China directory" if source == "ldc" else "campus directory"}
                save(store, "sources", "discovery-" + source, {"last_checked_at": now, "scope": result["scope"][source], "artifacts": artifacts}, "真实扫描范围与下一批进度")
            except (OSError, ValueError, KeyError) as exc:
                result["errors"].append({"source": source, "error": type(exc).__name__ + ": " + str(exc)})
                result["failed"] += 1
                checkpoint()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        checkpoint()
        result["status"] = "partial" if result["errors"] or result["remaining"] else "completed"
        checkpoint()
        return result


def main():
    parser = argparse.ArgumentParser(description="从企业当前目录发现新岗位，并分批读取详情与初筛")
    parser.add_argument("--batch", type=int, default=4, help="每个来源本轮最多读取的详情数量")
    parser.add_argument("--refresh", action="store_true", help="明确要求刷新已有详情")
    parser.add_argument("--retry", action="store_true", help="能力修复后明确重试，绕过失败退避")
    args = parser.parse_args()
    if not 1 <= args.batch <= 50:
        parser.error("batch 必须为 1..50")
    print(json.dumps(discover(batch=args.batch, refresh=args.refresh, retry=args.retry), ensure_ascii=False))


if __name__ == "__main__":
    main()
