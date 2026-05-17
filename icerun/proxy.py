"""Proxy pool management — rotation, health scoring, webshare API integration."""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ProxyPool:
    proxies: list[str] = field(default_factory=list)
    failed: set[str] = field(default_factory=set)
    _counter: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _usage: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _cache_expires: float = field(default=0.0, init=False, repr=False)
    _cache_ttl: int = field(default=300, init=False, repr=False)

    def _active(self) -> list[str]:
        return [p for p in self.proxies if p not in self.failed]

    @classmethod
    def from_env(cls) -> "ProxyPool":
        """Build proxy pool from ICER_PROXY or ICER_PROXY_API_KEY env vars."""
        single = os.environ.get("ICER_PROXY", "").strip()
        if single:
            return cls(proxies=[single])

        api_key = os.environ.get("ICER_PROXY_API_KEY", "").strip()
        if api_key:
            return cls._from_webshare(api_key)

        return cls()  # empty pool — direct connections

    @classmethod
    def _from_webshare(cls, api_key: str) -> "ProxyPool":
        """Fetch proxy list from webshare API."""
        import httpx
        proxies = []
        url = "https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100&valid=true"
        headers = {"Authorization": f"Token {api_key}"}
        try:
            while url:
                r = httpx.get(url, headers=headers, timeout=10)
                r.raise_for_status()
                data = r.json()
                for p in data.get("results", []):
                    proxy_url = f"http://{p['username']}:{p['password']}@{p['proxy_address']}:{p['port']}"
                    proxies.append(proxy_url)
                url = data.get("next")
        except Exception as e:
            raise RuntimeError(f"Failed to fetch webshare proxies: {e}") from e
        pool = cls(proxies=proxies)
        pool._cache_expires = time.monotonic() + pool._cache_ttl
        return pool

    @classmethod
    def from_file(cls, path: str) -> "ProxyPool":
        """Load proxy list from newline-separated file."""
        lines = Path(path).read_text().splitlines()
        proxies = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
        return cls(proxies=proxies)

    def get(self, sticky_key: Optional[str] = None) -> Optional[str]:
        """Return next proxy. Returns None for direct connection if pool is empty."""
        active = self._active()
        if not active:
            return None
        if sticky_key is not None:
            h = int(hashlib.md5(sticky_key.encode()).hexdigest(), 16)
            proxy = active[h % len(active)]
        else:
            with self._lock:
                proxy = active[self._counter % len(active)]
                self._counter += 1
        self._usage[proxy] = self._usage.get(proxy, 0) + 1
        return proxy

    def mark_failed(self, proxy: str) -> None:
        """Mark a proxy as failed and remove from rotation."""
        self.failed.add(proxy)

    def stats(self) -> dict:
        """Return pool statistics."""
        active = self._active()
        return {
            "total": len(self.proxies),
            "failed": len(self.failed),
            "active": len(active),
            "usage": dict(self._usage),
        }

    def reset_failed(self) -> None:
        """Clear failed set (e.g., after TTL refresh)."""
        self.failed.clear()

    def is_empty(self) -> bool:
        return len(self._active()) == 0
