import tempfile
import unittest
from pathlib import Path
from job_agent.store import Conflict, Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "test.sqlite3")

    def test_history_conflict_and_restore(self):
        original = {"raw_text": "中文 JD", "custom": {"unknown": True}}
        first = self.store.put("leads", original, "a")
        second = self.store.put("leads", {**original, "notes": "AI 判断"}, "a", first["version"], reason="补充")
        with self.assertRaises(Conflict):
            self.store.put("leads", {}, "a", first["version"])
        self.assertEqual(len(self.store.history("leads", "a")), 2)
        self.store.put("leads", original, "a", second["version"], reason="恢复")
        self.assertEqual(Store(self.store.path).get("leads", "a")["data"], original)
        self.assertEqual(len(self.store.export()["history"]), 3)

    def test_pagination_and_no_automatic_deduplication(self):
        for i in range(3):
            self.store.put("jobs", {"title": "同一个名称"}, str(i))
        page = self.store.list("jobs", 2)
        self.assertEqual(page["next_offset"], 2)
        tail = self.store.list("jobs", 2, page["next_offset"])
        self.assertEqual(len(tail["items"]), 1)
        self.assertIsNone(tail["next_offset"])
        self.assertEqual(len(self.store.export()["records"]), 3)

    def test_invalid_writes_do_not_change_data(self):
        with self.assertRaises(ValueError):
            self.store.put("jobs", [])
        with self.assertRaises(ValueError):
            self.store.put("jobs", {"score": float("nan")})
        with self.assertRaises(ValueError):
            self.store.put("invalid", {})
        self.assertEqual(self.store.export()["records"], [])


if __name__ == "__main__":
    unittest.main()
