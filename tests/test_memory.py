"""Тесты памяти: запись, гибридная выборка, фолбэки."""

from __future__ import annotations

import unittest

from _helpers import build_agent
from universal_agent import MemoryStore


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.agent, self.tmp = build_agent()
        self.store: MemoryStore = self.agent.memory

    def test_ids_grow_monotonically(self):
        first = self.store.record("t1", "fact", "первая", 0.5)
        second = self.store.record("t1", "fact", "вторая", 0.5)
        self.assertGreater(second, first)

    def test_unique_token_found(self):
        self.store.record("t1", "fact", "Феникс живёт в персиковом саду", 0.5)
        hits = self.store.recall("феникс", 3, 14.0)
        self.assertTrue(hits)
        self.assertIn("Феникс", hits[0]["content"])

    def test_better_match_ranks_first(self):
        self.store.record("t1", "fact", "тета случай", 0.5)
        self.store.record("t1", "fact", "тета йота случай", 0.5)
        hits = self.store.recall("тета йота", 2, 14.0)
        self.assertIn("йота", hits[0]["content"])

    def test_fresh_beats_stale(self):
        old_id = self.store.record("t1", "fact", "зета свежесть", 0.5)
        self.store.record("t1", "fact", "зета новизна", 0.5)
        # состариваем первую запись на 30 суток
        import time as _t
        self.store.db.execute(
            "UPDATE events SET ts = ? WHERE id = ?", (_t.time() - 30 * 86400, old_id)
        )
        self.store.db.commit()
        hits = self.store.recall("зета", 2, 14.0)
        self.assertIn("новизна", hits[0]["content"])

    def test_importance_persisted(self):
        rid = self.store.record("t1", "lesson", "всегда проверяй выход кода", 0.95)
        row = self.store.db.execute(
            "SELECT importance FROM events WHERE id = ?", (rid,)
        ).fetchone()
        self.assertAlmostEqual(row[0], 0.95)

    def test_like_fallback_when_no_fts(self):
        self.store.fts_enabled = False
        self.store.record("t1", "fact", "омега жемчужина", 0.5)
        hits = self.store.recall("омега", 3, 14.0)
        self.assertTrue(hits)
        self.assertIn("омега", hits[0]["content"])

    def test_no_match_returns_recent(self):
        self.store.record("t1", "fact", "обычная запись", 0.5)
        hits = self.store.recall(" completely_unrelated_token", 3, 14.0)
        self.assertTrue(hits)  # фолбэк на свежую хронику

    def test_empty_query_is_silence(self):
        self.assertEqual(self.store.recall("", 3, 14.0), [])


if __name__ == "__main__":
    unittest.main()
