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
