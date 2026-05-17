"""Tests for proxy pool module."""
import os
import pytest
from icerun.proxy import ProxyPool


def test_empty_pool_returns_none():
    pool = ProxyPool()
    assert pool.get() is None


def test_single_proxy_rotation():
    pool = ProxyPool(proxies=["http://user:pass@1.2.3.4:8080"])
    assert pool.get() == "http://user:pass@1.2.3.4:8080"
    assert pool.get() == "http://user:pass@1.2.3.4:8080"


def test_round_robin_rotation():
    pool = ProxyPool(proxies=["http://p1:8080", "http://p2:8080", "http://p3:8080"])
    results = [pool.get() for _ in range(6)]
    assert results[0] != results[1] or results[1] != results[2]
    assert set(results) == {"http://p1:8080", "http://p2:8080", "http://p3:8080"}


def test_mark_failed_excludes_proxy():
    pool = ProxyPool(proxies=["http://bad:8080", "http://good:8080"])
    pool.mark_failed("http://bad:8080")
    for _ in range(10):
        assert pool.get() == "http://good:8080"


def test_all_failed_returns_none():
    pool = ProxyPool(proxies=["http://p1:8080"])
    pool.mark_failed("http://p1:8080")
    assert pool.get() is None


def test_sticky_key_deterministic():
    pool = ProxyPool(proxies=["http://p1:8080", "http://p2:8080", "http://p3:8080"])
    p1 = pool.get(sticky_key="example.com")
    p2 = pool.get(sticky_key="example.com")
    assert p1 == p2


def test_sticky_key_different_domains():
    pool = ProxyPool(proxies=["http://p1:8080", "http://p2:8080", "http://p3:8080"])
    results = {pool.get(sticky_key=f"domain{i}.com") for i in range(9)}
    assert len(results) > 1  # different domains hash to different proxies


def test_stats():
    pool = ProxyPool(proxies=["http://p1:8080", "http://p2:8080"])
    pool.get()
    pool.mark_failed("http://p1:8080")
    s = pool.stats()
    assert s["total"] == 2
    assert s["failed"] == 1
    assert s["active"] == 1


def test_from_env_empty(monkeypatch):
    monkeypatch.delenv("ICER_PROXY", raising=False)
    monkeypatch.delenv("ICER_PROXY_API_KEY", raising=False)
    pool = ProxyPool.from_env()
    assert pool.get() is None


def test_from_env_single_proxy(monkeypatch):
    monkeypatch.setenv("ICER_PROXY", "http://user:pass@1.2.3.4:8080")
    monkeypatch.delenv("ICER_PROXY_API_KEY", raising=False)
    pool = ProxyPool.from_env()
    assert pool.get() == "http://user:pass@1.2.3.4:8080"


def test_from_file(tmp_path):
    pf = tmp_path / "proxies.txt"
    pf.write_text("http://p1:8080\nhttp://p2:8080\n# comment\n\n")
    pool = ProxyPool.from_file(str(pf))
    assert len(pool.proxies) == 2
    assert "http://p1:8080" in pool.proxies
