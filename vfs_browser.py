"""Browser launcher and session manager for VFS Global Automation."""

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple
from playwright.sync_api import BrowserContext, Page, Playwright

log = logging.getLogger("vfs.browser")


def get_profile_dir(channel: str = "brave") -> Path:
    """Get clean dedicated profile path in LocalAppData without spaces."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        p = Path(local_app_data) / f"vfs-{channel}-profile"
    else:
        p = Path(__file__).resolve().parent / "data" / f"{channel}-profile"
    p.mkdir(parents=True, exist_ok=True)
    return p


def find_browser_executable(preferred: str = "brave") -> Path:
    """Find installed browser binary on Windows or Linux."""
    if sys.platform == "win32":
        roots = [
            os.environ.get("LOCALAPPDATA", ""),
            os.environ.get("PROGRAMFILES", ""),
            os.environ.get("PROGRAMFILES(X86)", ""),
        ]
        candidates = []
        if preferred == "brave":
            candidates.append("BraveSoftware/Brave-Browser/Application/brave.exe")
        elif preferred in ("chrome", "google-chrome"):
            candidates.append("Google/Chrome/Application/chrome.exe")
        
        # General fallbacks
        candidates.extend([
            "BraveSoftware/Brave-Browser/Application/brave.exe",
            "Google/Chrome/Application/chrome.exe",
            "Microsoft/Edge/Application/msedge.exe",
        ])

        for root in roots:
            if not root:
                continue
            for sub in candidates:
                p = Path(root) / sub
                if p.exists():
                    log.info(f"Found browser binary: {p}")
                    return p
    else:
        # Linux VPS standard paths
        for binary in ["/usr/bin/brave-browser", "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]:
            p = Path(binary)
            if p.exists():
                return p

    return None


def bring_browser_to_front():
    """Bring the active browser window directly to the front on Windows desktop."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        # Connect thread to interactive desktop
        h_desk = user32.OpenInputDesktop(0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)

        user32.AllowSetForegroundWindow(-1)

        def callback(hwnd, extra):
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()
                    if any(k in title for k in ["brave", "chrome", "vfs", "4140"]):
                        user32.keybd_event(0x12, 0, 0, 0)
                        user32.keybd_event(0x12, 0, 2, 0)
                        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
                        user32.BringWindowToTop(hwnd)
                        user32.SetForegroundWindow(hwnd)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(callback), 0)
    except Exception as e:
        log.debug(f"bring_to_front note: {e}")

# Alias for compatibility
bring_window_to_front_win32 = bring_browser_to_front


def launch_stealth_browser(
    playwright: Playwright,
    channel: str = "brave",
    headless: bool = False,
    user_data_dir: Optional[Path] = None,
    dashboard_url: str = "http://127.0.0.1:4140"
) -> Tuple[BrowserContext, Page]:
    """Launch resilient browser session."""
    exe = find_browser_executable(preferred=channel)
    profile_dir = user_data_dir or get_profile_dir(channel)

    # Clear stale SingletonLock if exists
    lock = profile_dir / "SingletonLock"
    if lock.exists():
        try:
            lock.unlink()
        except Exception:
            pass

    args = [
        "--start-maximized",
        "--no-default-browser-check",
        "--no-first-run",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
    ]

    launch_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": headless,
        "args": args,
        "no_viewport": not headless,
        "ignore_default_args": ["--enable-automation"],
    }

    if exe and exe.exists():
        launch_kwargs["executable_path"] = str(exe)
    elif channel and channel != "chromium":
        launch_kwargs["channel"] = channel

    log.info(f"Launching persistent browser context (exe={exe}, profile={profile_dir})...")
    context = playwright.chromium.launch_persistent_context(**launch_kwargs)

    # Single active tab focused directly on VFS portal
    if context.pages:
        vfs_page = context.pages[0]
    else:
        vfs_page = context.new_page()

    if not headless:
        try:
            vfs_page.bring_to_front()
        except Exception:
            pass
        if sys.platform == "win32":
            time.sleep(0.5)
            bring_browser_to_front()

    return context, vfs_page
