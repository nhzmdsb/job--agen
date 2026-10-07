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
                        {"list_records", "read_record", "write_record", "read_history"})
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

        with tempfile.TemporaryDirectory() as root:
            asyncio.run(run(Path(root) / "mcp.sqlite3"))
