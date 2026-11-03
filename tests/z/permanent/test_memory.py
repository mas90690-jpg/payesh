"""تست‌های core/brain/memory.py — SmartCache."""

from __future__ import annotations

import time

import pytest

from core.brain.memory import SmartCache


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr("core.brain.memory.CACHE_DIR", tmp_path)
    return SmartCache(name="test_cache", ttl_days=1, max_size=10)


class TestSmartCache:
    def test_set_and_get(self, cache):
        cache.set("question", "answer")
        assert cache.get("question") == "answer"

    def test_get_missing(self, cache):
        assert cache.get("__nope__") is None

    def test_set_empty_text_ignored(self, cache):
        cache.set("", "answer")
        assert cache.get("") is None

    def test_set_empty_answer_ignored(self, cache):
        cache.set("q", "")
        assert cache.get("q") is None

    def test_model_separation(self, cache):
        cache.set("q", "a1", model="m1")
        cache.set("q", "a2", model="m2")
        assert cache.get("q", model="m1") == "a1"
        assert cache.get("q", model="m2") == "a2"

    def test_ttl_expiry(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.brain.memory.CACHE_DIR", tmp_path)
        c = SmartCache(name="ttl_test", ttl_days=0, max_size=10)
        c.set("q", "a")
        # ttl=0 → بلافاصله منقضی
        time.sleep(0.01)
        assert c.get("q") is None

    def test_lru_eviction(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.brain.memory.CACHE_DIR", tmp_path)
        c = SmartCache(name="lru_test", ttl_days=1, max_size=5)
        for i in range(10):
            c.set(f"q{i}", f"a{i}")
        assert len(c.cache) <= 5

    def test_clear(self, cache):
        cache.set("q", "a")
        cache.clear()
        assert cache.get("q") is None
        assert cache.stats()["entries"] == 0

    def test_stats_structure(self, cache):
        cache.set("q", "a")
        cache.get("q")
        cache.get("__miss__")
        s = cache.stats()
        assert "entries" in s
        assert "hits" in s
        assert "misses" in s
        assert "hit_rate" in s
        assert s["hits"] >= 1
        assert s["misses"] >= 1

    def test_get_stats_alias(self, cache):
        assert cache.get_stats() == cache.stats()

    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.brain.memory.CACHE_DIR", tmp_path)
        c1 = SmartCache(name="persist", ttl_days=1, max_size=10)
        c1.set("q", "a")
        c1.save()
        c2 = SmartCache(name="persist", ttl_days=1, max_size=10)
        assert c2.get("q") == "a"

    def test_load_corrupt_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.brain.memory.CACHE_DIR", tmp_path)
        f = tmp_path / "corrupt.json"
        f.write_text("{bad json", encoding="utf-8")
        c = SmartCache(name="corrupt", ttl_days=1, max_size=10)
        assert c.cache == {}

    def test_flush_when_dirty(self, cache):
        cache.set("q", "a")
        cache.flush()  # نباید خطا بده
