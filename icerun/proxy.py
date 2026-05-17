"""Proxy pool management — rotation, health scoring, per-request assignment."""
from dataclasses import dataclass, field
from typing import Optional
import os


@dataclass
class ProxyPool:
    proxies: list[str] = field(default_factory=list)
    failed: set[str] = field(default_factory=set)
    _counter: int = 0

    @classmethod
    def from_env(cls) -> "ProxyPool":
        """Build proxy pool from ICER_PROXY or ICER_PROXY_API_KEY env vars."""
        raise NotImplementedError("ProxyPool.from_env not yet implemented")

    @classmethod
    def from_file(cls, path: str) -> "ProxyPool":
        """Load proxy list from newline-separated file."""
        raise NotImplementedError("ProxyPool.from_file not yet implemented")

    def get(self, sticky_key: Optional[str] = None) -> Optional[str]:
        """Return next proxy. None = direct connection."""
        raise NotImplementedError("ProxyPool.get not yet implemented")

    def mark_failed(self, proxy: str) -> None:
        self.failed.add(proxy)

    def stats(self) -> dict:
        return {"total": len(self.proxies), "failed": len(self.failed), "active": len(self.proxies) - len(self.failed)}
