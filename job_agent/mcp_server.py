from typing import Any
from mcp.server.fastmcp import FastMCP
from .store import Store

mcp = FastMCP("job-agent-workspace")


@mcp.tool()
def list_records(collection: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Page through sources/leads/jobs/runs/artifacts/profiles. Follow next_offset."""
    return Store().list(collection, limit, offset)


@mcp.tool()
def read_record(collection: str, record_id: str) -> dict[str, Any]:
    """Read the complete record and its version before editing."""
    return Store().get(collection, record_id)


@mcp.tool()
def write_record(collection: str, data: dict[str, Any], record_id: str | None = None,
                 expected_version: int = 0, actor: str = "agent", reason: str = "",
                 quota_confirm: bool = False, quota_reason: str = "", human_confirm: bool = False,
                 confirmation_reason: str = "") -> dict[str, Any]:
    """Create or CAS update; jobs recursively preserve omitted fields. Structured jobs enforce tombstone deduplication and quotas; overrides require explicit user confirmation and a reason."""
    return Store().put(collection, data, record_id, expected_version, actor, reason, quota_confirm=quota_confirm, quota_reason=quota_reason, human_confirm=human_confirm, confirmation_reason=confirmation_reason)


@mcp.tool()
def worker_context(limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Confirmed conditions and paginated unfinished steps, without record history."""
    from .worker import context
    return context(Store(), limit, offset)


@mcp.tool()
def lookup_jobs(identities: list[dict[str, Any]]) -> dict[str, Any]:
    """Batch lookup source IDs and URLs, including aliases, trash and merged records."""
    from .worker import lookup
    return lookup(Store(), identities)


@mcp.tool()
def patch_record(collection: str, record_id: str, patch: dict[str, Any], expected_version: int,
                 reason: str = "", human_confirm: bool = False, confirmation_reason: str = "",
                 quota_confirm: bool = False, quota_reason: str = "") -> dict[str, Any]:
    """CAS selective update: recursive objects, replacement arrays; ask before changing human judgments."""
    from .worker import write
    return write(Store(), collection, patch, record_id, expected_version, patch=True, reason=reason, human_confirm=human_confirm, confirmation_reason=confirmation_reason, quota_confirm=quota_confirm, quota_reason=quota_reason)


@mcp.tool()
def merge_jobs(target_id: str, other_id: str, target_version: int, other_version: int,
               reason: str, same_job_confirm: bool = False) -> dict[str, Any]:
    """Explicit same-job merge with both versions; preserve both sources and evidence."""
    from .worker import merge_jobs as merge
    return merge(Store(), target_id, other_id, target_version, other_version, reason, same_job_confirm)


@mcp.tool()
def read_history(collection: str, record_id: str) -> list[dict[str, Any]]:
    """Read saved revisions with actor and reason; restore by writing an old data object at the current version."""
    return Store().history(collection, record_id)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
