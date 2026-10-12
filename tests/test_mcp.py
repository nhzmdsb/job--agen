import asyncio
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec("mcp"), "install the mcp extra to test stdio")
class MCPTests(unittest.TestCase):
    def test_stdio_roundtrip(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def run(path):
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "job_agent.mcp_server"],
                env={**os.environ, "JOB_AGENT_DB": str(path)})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools},
                        {"list_records", "read_record", "write_record", "read_history", "worker_context", "lookup_jobs", "patch_record", "merge_jobs"})
                    created = await session.call_tool("write_record", {
                        "collection": "sources", "record_id": "demo",
                        "data": {"name": "测试来源", "enabled": False}})
                    self.assertFalse(created.isError)
                    self.assertEqual(created.structuredContent["version"], 1)
                    read_result = await session.call_tool("read_record", {
                        "collection": "sources", "record_id": "demo"})
                    self.assertEqual(read_result.structuredContent["data"]["name"], "测试来源")
                    listed = await session.call_tool("list_records", {"collection": "sources"})
                    self.assertEqual(len(listed.structuredContent["items"]), 1)
                    edited = await session.call_tool("write_record", {
                        "collection": "sources", "record_id": "demo", "expected_version": 1,
                        "data": {"name": "更新来源", "enabled": False}, "reason": "测试修改"})
                    self.assertFalse(edited.isError)
                    stale = await session.call_tool("write_record", {
                        "collection": "sources", "record_id": "demo", "expected_version": 1,
                        "data": {}})
                    self.assertTrue(stale.isError)
                    history = await session.call_tool("read_history", {
                        "collection": "sources", "record_id": "demo"})
                    self.assertFalse(history.isError)
                    self.assertEqual(len(history.structuredContent["result"]), 2)
                    blocked = await session.call_tool("write_record", {
                        "collection": "jobs", "record_id": "human", "expected_version": 1,
                        "actor": "workbench", "data": {"color": "yellow", "color_reason": "Changed"}})
                    self.assertTrue(blocked.isError)
                    edited = await session.call_tool("patch_record", {
                        "collection": "jobs", "record_id": "human", "expected_version": 1,
                        "patch": {"notes": "Agent note"}})
                    self.assertFalse(edited.isError)
                    self.assertEqual(edited.structuredContent["version"], 2)
                    blocked = await session.call_tool("patch_record", {
                        "collection": "jobs", "record_id": "human", "expected_version": 2,
                        "patch": {"human_judgment": "Changed"}})
                    self.assertTrue(blocked.isError)
                    confirmed = await session.call_tool("patch_record", {
                        "collection": "jobs", "record_id": "human", "expected_version": 2,
                        "patch": {"human_judgment": "Agreed change"}, "human_confirm": True,
                        "confirmation_reason": "Fixture explicit agreement", "reason": "Reviewed fixture evidence"})
                    self.assertFalse(confirmed.isError)
                    stale = await session.call_tool("patch_record", {
                        "collection": "jobs", "record_id": "human", "expected_version": 2,
                        "patch": {"notes": "Overwrite"}})
                    self.assertTrue(stale.isError)

        with tempfile.TemporaryDirectory() as root:
            from job_agent.store import Store
            path = Path(root) / "mcp.sqlite3"
            Store(path).put("jobs", {"company": "Fictional Co", "title": "虚构岗位", "url": "https://example.invalid/fixture",
                "color": "green", "color_reason": "Human choice", "human_judgment": "Human judgment"}, "human", _human=True)
            asyncio.run(run(path))
