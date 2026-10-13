"""Shared incremental worker operations for CLI, MCP and HTTP."""
from .jobs import identity_keys
from .review import review_queue
from .store import Conflict, merge_fields


def context(store, limit=50, offset=0):
    return review_queue(store, limit, offset)


def lookup(store, identities):
    if not isinstance(identities, list) or len(identities) > 200 or any(not isinstance(e, dict) for e in identities):
        raise ValueError("identities must be an array of at most 200 objects")
    records = store.records("jobs")
    return {"items": [{"identity": entry, "matches": [r for r in records if identity_keys(entry) & identity_keys(r["data"])]} for entry in identities]}


def write(store, collection, data, record_id=None, expected_version=0, *, patch=False, **options):
    record = store.patch(collection, record_id, data, expected_version, **options) if patch else store.put(collection, data, record_id, expected_version, **options)
    return {"record": record, "version": record["version"], "action": "unchanged" if record["version"] == expected_version else "updated" if expected_version else "created"}


def merge_jobs(store, target_id, other_id, target_version, other_version, reason, same_job_confirm=False):
    if target_id == other_id or same_job_confirm is not True or not isinstance(reason, str) or not reason.strip():
        raise ValueError("确认同一岗位、填写依据，并提供两个不同记录及双方版本")
    if any(type(v) is not int or v < 1 for v in (target_version, other_version)):
        raise ValueError("both versions must be positive integers")
    with store.connection() as db:
        db.execute("BEGIN IMMEDIATE")
        def read(record_id, version):
            record = store.decode(db.execute("SELECT * FROM records WHERE collection='jobs' AND id=?", (record_id,)).fetchone())
            if record["version"] != version:
                raise Conflict("合并版本冲突；重新读取双方后再确认")
            if record["data"].get("merged_into") or record["data"].get("stage") == "trash":
                raise Conflict("已合并或回收站记录不能再次合并")
            return record
        target, other = read(target_id, target_version), read(other_id, other_version)
        # Keep the donor intact, and link its full evidence on the surviving record.
        donor = store.put("jobs", {"merged_into": target_id}, other_id, other_version, reason=reason, _db=db, _merge=True)
        data = target["data"]
        aliases = data.get("identity_aliases", []) + [{k: other["data"][k] for k in ("source", "discovery_source", "source_job_id", "url", "detail_url", "boss_url") if k in other["data"]}] + other["data"].get("identity_aliases", [])
        evidence = data.get("merged_evidence", []) + [{"id": other_id, "version": other_version, "data": other["data"], "reason": reason}]
        result = store.put("jobs", merge_fields(data, {"identity_aliases": aliases, "merged_evidence": evidence, "dedupe_keys": sorted(identity_keys(data) | identity_keys(other["data"])), "artifacts": list(dict.fromkeys(data.get("artifacts", []) + other["data"].get("artifacts", [])))}), target_id, target_version, reason=reason, _db=db, _merge=True)
        return {"record": result, "merged": donor, "version": result["version"], "action": "updated"}
