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

_LAST_BROWSER_HWND = None


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


def bring_browser_to_front(page: Optional[Page] = None):
    """Bring the REAL active VFS browser window directly in front of the user on Windows desktop.
    
    Restores the window if minimized, maximizes it, pushes it to top of Z-order,
    and sets focus so the user can directly see actions, type details, or make payments.
    Explicitly ignores the 4140 Flask Control Dashboard tab.
    """
    global _LAST_BROWSER_HWND
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 1. First trigger Playwright's native bring_to_front
        if page:
            try:
                page.bring_to_front()
            except Exception:
                pass

        # 2. Allow foreground window changes
        h_desk = user32.OpenInputDesktop(0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)

        user32.AllowSetForegroundWindow(-1)

        vfs_targets = []

        def callback(hwnd, extra):
            # We look for visible or minimized top-level windows
            if user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()

                    # Check window class name (Chromium is always Chrome_WidgetWin_1)
                    class_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buf, 256)
                    class_name = class_buf.value

                    # NEVER match the Control Dashboard window!
                    if any(k in title for k in ["4140", "automation suite", "live controller", "localhost:"]):
                        return True

                    is_chromium = ("chrome_widgetwin" in class_name.lower())

                    # Target VFS Global browser window
                    if any(k in title for k in [
                        "visa.vfsglobal", "vfs.global", "vfs global", "welcome to vfs",
                        "book an appointment", "bulgaria", "application-detail",
                        "your details", "appointment-detail", "service"
                    ]):
                        vfs_targets.append((hwnd, 1))  # Highest priority
                    elif is_chromium and any(k in title for k in ["vfs", "appointment", "visa", "sign in", "login"]):
                        vfs_targets.append((hwnd, 2))
                    elif is_chromium and any(k in title for k in ["brave", "chrome"]):
                        # Chromium window running separate profile
                        vfs_targets.append((hwnd, 3))
                    elif "vfs" in title:
                        vfs_targets.append((hwnd, 4))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(callback), 0)

        # Sort by priority
        vfs_targets.sort(key=lambda x: x[1])

        target_hwnd = None
        if vfs_targets:
            target_hwnd = vfs_targets[0][0]
            _LAST_BROWSER_HWND = target_hwnd
        elif _LAST_BROWSER_HWND and user32.IsWindow(_LAST_BROWSER_HWND):
            target_hwnd = _LAST_BROWSER_HWND

        if target_hwnd:
            # 1. Un-minimize if iconic
            if user32.IsIconic(target_hwnd):
                user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
                time.sleep(0.05)

            # 2. Maximize
            user32.ShowWindow(target_hwnd, 3)  # SW_MAXIMIZE

            # 3. Force window to top of Z-order
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040

            user32.SetWindowPos(target_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.SetWindowPos(target_hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)

            # 4. Attach thread input to bypass Windows LockSetForegroundWindow
            fore_hwnd = user32.GetForegroundWindow()
            fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, None)
            cur_thread = kernel32.GetCurrentThreadId()

            if fore_thread and fore_thread != cur_thread:
                user32.AttachThreadInput(cur_thread, fore_thread, True)
                user32.BringWindowToTop(target_hwnd)
                user32.SetForegroundWindow(target_hwnd)
                user32.AttachThreadInput(cur_thread, fore_thread, False)
            else:
                user32.BringWindowToTop(target_hwnd)
                user32.SetForegroundWindow(target_hwnd)

            # 5. Set focus
            user32.keybd_event(0x12, 0, 0, 0)
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetFocus(target_hwnd)
            log.info(f"Brought real browser window (HWND={target_hwnd}) to front and maximized.")
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
            time.sleep(1.0)
            bring_browser_to_front(vfs_page)

    return context, vfs_page
