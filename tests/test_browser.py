"""Tests for browser module (import-level and unit tests — no live browser needed)."""
import pytest
from icerun.browser import parse_proxy_for_browser, execute_action


def test_parse_proxy_http():
    result = parse_proxy_for_browser("http://user:pass@1.2.3.4:8080")
    assert result["server"] == "http://1.2.3.4:8080"
    assert result["username"] == "user"
    assert result["password"] == "pass"


def test_parse_proxy_no_auth():
    result = parse_proxy_for_browser("http://1.2.3.4:8080")
    assert result["server"] == "http://1.2.3.4:8080"
    assert "username" not in result


def test_parse_proxy_socks5():
    result = parse_proxy_for_browser("socks5://user:pass@10.0.0.1:1080")
    assert result["server"] == "socks5://10.0.0.1:1080"


@pytest.mark.asyncio
async def test_execute_action_all_types():
    """Test all action types via mock page — no live browser needed."""

    class FakePage:
        async def click(self, *a, **kw):
            pass

        async def evaluate(self, *a, **kw):
            pass

        async def fill(self, *a, **kw):
            pass

        async def wait_for_selector(self, *a, **kw):
            pass

    page = FakePage()
    # These should not raise
    await execute_action(page, "click:#btn")
    await execute_action(page, "scroll:bottom")
    await execute_action(page, "scroll:top")
    await execute_action(page, "scroll:100px")
    await execute_action(page, "scroll:500")
    await execute_action(page, "fill:#input=hello")
    await execute_action(page, "fill:#name=John Doe")
    await execute_action(page, "wait_for:#element")


@pytest.mark.asyncio
async def test_execute_action_wait_sleeps():
    """wait: action should sleep for the specified duration."""
    import time

    start = time.monotonic()
    # Use a very short sleep to keep tests fast
    await execute_action(object(), "wait:0.05")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.04, f"Expected sleep ~0.05s, got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_execute_action_unknown_raises():
    class FakePage:
        pass

    with pytest.raises(ValueError, match="Unknown browser action"):
        await execute_action(FakePage(), "unknown:action")


@pytest.mark.asyncio
async def test_execute_action_unknown_bare_raises():
    """A bare string with no colon should also raise ValueError."""

    class FakePage:
        pass

    with pytest.raises(ValueError, match="Unknown browser action"):
        await execute_action(FakePage(), "justplaintext")


def test_require_camoufox_importable():
    """camoufox should be importable in the browser extra."""
    try:
        import camoufox  # noqa: F401
        from icerun.browser import browser_fetch

        assert callable(browser_fetch)
    except ImportError:
        pytest.skip("camoufox not installed")


def test_browser_fetch_importable():
    """browser_fetch and all public names are importable from browser module."""
    from icerun.browser import browser_fetch, parse_proxy_for_browser, execute_action

    assert callable(browser_fetch)
    assert callable(parse_proxy_for_browser)
    assert callable(execute_action)


def test_parse_proxy_returns_dict():
    """Return value is always a plain dict with at least 'server' key."""
    result = parse_proxy_for_browser("http://proxy.example.com:3128")
    assert isinstance(result, dict)
    assert "server" in result
    assert result["server"] == "http://proxy.example.com:3128"
