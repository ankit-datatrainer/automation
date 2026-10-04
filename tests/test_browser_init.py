import pytest
from playwright.sync_api import sync_playwright
from vfs_browser import launch_stealth_browser

def test_stealth_browser_launch(tmp_path):
    with sync_playwright() as p:
        context, page = launch_stealth_browser(
            p,
            channel="chrome",
            headless=True,
            user_data_dir=tmp_path / "test_profile"
        )
        assert context is not None
        assert page is not None

        # Verify stealth script execution
        page.goto("https://httpbin.org/get")
        webdriver_prop = page.evaluate("navigator.webdriver")
        assert webdriver_prop in (None, False) or not webdriver_prop

        context.close()


def test_unsupported_channel_fallback(tmp_path, monkeypatch):
    """Verify that if an unsupported channel like 'brave' has no executable, it falls back cleanly to bundled Chromium."""
    import vfs_browser
    monkeypatch.setattr(vfs_browser, "find_browser_executable", lambda preferred="brave": None)

    with sync_playwright() as p:
        context, page = launch_stealth_browser(
            p,
            channel="brave",
            headless=True,
            user_data_dir=tmp_path / "fallback_profile"
        )
        assert context is not None
        assert page is not None
        context.close()
