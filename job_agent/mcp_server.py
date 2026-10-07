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
                 expected_version: int = 0, actor: str = "agent", reason: str = "") -> dict[str, Any]:
    """Create (expected_version=0) or replace using the version you read. Supply the entire data object, preserving fields. No automatic merge, deduplication or evaluation."""
    return Store().put(collection, data, record_id, expected_version, actor, reason)


@mcp.tool()
def read_history(collection: str, record_id: str) -> list[dict[str, Any]]:
    """Read saved revisions with actor and reason; restore by writing an old data object at the current version."""
    return Store().history(collection, record_id)


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
