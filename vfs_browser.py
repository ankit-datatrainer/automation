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
        candidates = []
        if preferred == "brave":
            candidates.extend(["/usr/bin/brave-browser", "/usr/bin/brave", "/snap/bin/brave"])
        elif preferred in ("chrome", "google-chrome"):
            candidates.extend(["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"])

        candidates.extend([
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/snap/bin/chromium",
            "/usr/bin/brave-browser",
            "/usr/bin/brave",
        ])
        for binary in candidates:
            p = Path(binary)
            if p.exists():
                log.info(f"Found Linux browser binary: {p}")
                return p

    return None


def attach_interactive_desktop():
    """Ensure the calling thread is attached to the interactive Windows desktop (WinSta0\\Default)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        winsta0 = user32.OpenWindowStationW("WinSta0", False, 0x037F)
        if winsta0:
            user32.SetProcessWindowStation(winsta0)
            desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if desk:
                user32.SetThreadDesktop(desk)
                return desk
    except Exception as e:
        log.debug(f"attach_interactive_desktop note: {e}")
    return None


def bring_browser_to_front(page: Optional[Page] = None):
    """Bring the REAL active VFS browser window directly in front of the user on Windows desktop.
    
    Restores the window if minimized, maximizes it, pushes it to top of Z-order,
    and sets focus so the operator can directly view clicks, enter OTP, fill forms, or make payments.
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

        # 1. Attach thread to user's interactive desktop WinSta0\Default
        attach_interactive_desktop()

        # 2. Tag page title if page object is available so it is 100% uniquely identifiable
        if page:
            try:
                page.bring_to_front()
                page.evaluate("() => { window.__vfs_automation_active = true; if (!document.title.includes('VFS_SESSION')) { document.title = 'VFS_SESSION - ' + (document.title || 'VFS Global'); } }")
            except Exception:
                pass

        user32.AllowSetForegroundWindow(-1)

        vfs_targets = []

        def callback(hwnd, extra):
            if user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()

                    class_buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, class_buf, 256)
                    class_name = class_buf.value.lower()

                    # NEVER match the Control Dashboard window!
                    if any(k in title for k in ["4140", "automation suite", "live controller", "localhost:"]):
                        return True

                    is_chromium = ("chrome_widgetwin" in class_name)

                    # Priority 0: Exact session tag
                    if "vfs_session" in title:
                        vfs_targets.append((hwnd, 0))
                    # Priority 1: Direct VFS Global URL or page title
                    elif any(k in title for k in [
                        "visa.vfsglobal", "vfs.global", "vfs global", "welcome to vfs",
                        "book an appointment", "bulgaria", "application-detail",
                        "your details", "appointment-detail", "service"
                    ]):
                        vfs_targets.append((hwnd, 1))
                    # Priority 2: Chromium window with auth/appointment titles
                    elif is_chromium and any(k in title for k in ["vfs", "appointment", "visa", "sign in", "login"]):
                        vfs_targets.append((hwnd, 2))
                    # Priority 3: Chromium browser window running separate profile
                    elif is_chromium and any(k in title for k in ["brave", "chrome"]):
                        vfs_targets.append((hwnd, 3))
                    elif "vfs" in title:
                        vfs_targets.append((hwnd, 4))
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
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
            # 1. Un-minimize if iconic (SW_RESTORE = 9)
            if user32.IsIconic(target_hwnd):
                user32.ShowWindow(target_hwnd, 9)
                time.sleep(0.05)

            # 2. Maximize window (SW_MAXIMIZE = 3)
            user32.ShowWindow(target_hwnd, 3)

            # 3. SwitchToThisWindow forces switch across modern Windows 11 virtual desktops & apps
            try:
                user32.SwitchToThisWindow(target_hwnd, True)
            except Exception:
                pass

            # 4. Use WScript.Shell AppActivate (bypasses Windows 11 foreground lock)
            try:
                import win32com.client
                wscript = win32com.client.Dispatch("WScript.Shell")
                wscript.AppActivate(target_hwnd)
            except Exception:
                pass

            # 5. Topmost toggle to guarantee Z-order elevation over other windows
            HWND_TOPMOST = -1
            HWND_NOTOPMOST = -2
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_SHOWWINDOW = 0x0040

            user32.SetWindowPos(target_hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            user32.SetWindowPos(target_hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)

            # 6. Attach thread input and bring to top
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

            # 7. Focus window
            user32.SetFocus(target_hwnd)
            log.info(f"Brought real browser window (HWND={target_hwnd}) to front and maximized.")
    except Exception as e:
        log.debug(f"bring_to_front note: {e}")

# Alias for compatibility
bring_window_to_front_win32 = bring_browser_to_front


def setup_performance_routes(page: Page):
    """Block slow tracking scripts and third-party analytics to maximize page responsiveness."""
    try:
        def handle_route(route):
            url = route.request.url.lower()
            # Never block VFS Global or Cloudflare security components
            if any(k in url for k in ["vfsglobal", "cloudflare", "turnstile"]):
                route.continue_()
                return
            # Block heavy tracking, analytics, and telemetry that slow down the browser
            if any(k in url for k in [
                "google-analytics.com", "googletagmanager.com", "doubleclick.net",
                "connect.facebook.net", "clarity.ms", "hotjar.com",
                "quantserve.com", "scorecardresearch.com"
            ]):
                route.abort()
            else:
                route.continue_()
        page.route("**/*", handle_route)
    except Exception as e:
        log.debug(f"Route blocking setup note: {e}")


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
)

DEFAULT_EXTRA_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "sec-ch-ua": '"Not(A:Brand";v="99", "Google Chrome";v="133", "Chromium";v="133"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

STEALTH_INIT_SCRIPT = """
// 1. Hide webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// 2. Mock chrome runtime & app
window.chrome = {
  app: { isInstalled: false },
  webstore: { onInstallStageChanged: {}, onDownloadProgress: {} },
  runtime: {
    PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux' },
    PlatformArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64' },
    RequestUpdateCheckStatus: { THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available' }
  }
};

// 3. Mock plugins and languages
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

// 4. Mock permissions query
if (window.navigator && window.navigator.permissions) {
  const originalQuery = window.navigator.permissions.query;
  window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
      Promise.resolve({ state: Notification.permission }) :
      originalQuery(parameters)
  );
}
"""


def ensure_linux_display() -> Optional[str]:
    """Ensure Linux has an active X display (starts Xvfb if DISPLAY is not present)."""
    if sys.platform == "win32":
        return None
    
    display = os.environ.get("DISPLAY")
    if display:
        return display

    import shutil
    xvfb_bin = shutil.which("Xvfb")
    if xvfb_bin:
        disp = ":99"
        try:
            p = subprocess.Popen(
                ["Xvfb", disp, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            os.environ["DISPLAY"] = disp
            log.info(f"Started Xvfb virtual display on {disp} (PID {p.pid})")
            return disp
        except Exception as e:
            log.warning(f"Could not start Xvfb: {e}")
    return None


def launch_stealth_browser(
    playwright: Playwright,
    channel: str = "brave",
    headless: bool = False,
    user_data_dir: Optional[Path] = None,
    dashboard_url: str = "http://127.0.0.1:4140"
) -> Tuple[BrowserContext, Page]:
    """Launch high-performance resilient browser session with full anti-bot stealth protection."""
    exe = find_browser_executable(preferred=channel)
    profile_dir = user_data_dir or get_profile_dir(channel)

    # Clear stale SingletonLock if exists
    lock = profile_dir / "SingletonLock"
    if lock.exists():
        try:
            lock.unlink()
        except Exception:
            pass

    # On Linux VPS: if Xvfb display is active, run headed (headless=False)
    # because headed mode inside Xvfb completely bypasses Cloudflare 403201 bot detection
    actual_headless = headless
    if sys.platform != "win32":
        disp = ensure_linux_display()
        if disp:
            actual_headless = False
            log.info(f"Linux virtual display detected ({disp}). Running browser headed inside Xvfb to bypass Cloudflare bot detection.")

    args = [
        "--start-maximized",
        "--window-size=1920,1080",
        "--no-default-browser-check",
        "--no-first-run",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--remote-debugging-port=9222",
        "--disable-blink-features=AutomationControlled",
        "--disable-infobars",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--disable-component-update",
    ]

    launch_kwargs = {
        "user_data_dir": str(profile_dir),
        "headless": actual_headless,
        "args": args,
        "user_agent": DEFAULT_USER_AGENT,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-US",
        "timezone_id": "Asia/Kolkata",
        "extra_http_headers": DEFAULT_EXTRA_HEADERS,
        "ignore_default_args": ["--enable-automation"],
    }

    # Playwright natively supports these channel names only
    VALID_PLAYWRIGHT_CHANNELS = {
        "chrome", "chrome-beta", "chrome-dev", "chrome-canary",
        "msedge", "msedge-beta", "msedge-dev", "msedge-canary"
    }

    req_channel = (channel or "").strip().lower()
    if exe and exe.exists():
        launch_kwargs["executable_path"] = str(exe)
    elif req_channel in VALID_PLAYWRIGHT_CHANNELS:
        launch_kwargs["channel"] = req_channel
    else:
        # If brave, chromium, or any unsupported channel where no executable was found,
        # do NOT pass invalid channel name like 'brave' to Playwright.
        # Playwright will automatically launch its bundled Chromium cleanly!
        log.info(f"Launching with bundled Playwright Chromium (channel '{channel}' has no binary path)")

    context = None
    last_err = None

    # Step 1: Attempt launch with preferred parameters
    try:
        log.info(f"Launching persistent browser context (exe={launch_kwargs.get('executable_path')}, channel={launch_kwargs.get('channel')}, profile={profile_dir})...")
        context = playwright.chromium.launch_persistent_context(**launch_kwargs)
    except Exception as e1:
        last_err = e1
        log.warning(f"Preferred browser launch failed: {e1}. Falling back to standard bundled Playwright Chromium...")

    # Step 2: Fallback to standard bundled Chromium without custom channel/executable
    if not context:
        fallback_kwargs = dict(launch_kwargs)
        fallback_kwargs.pop("channel", None)
        fallback_kwargs.pop("executable_path", None)
        try:
            log.info("Attempting launch with standard bundled Playwright Chromium...")
            context = playwright.chromium.launch_persistent_context(**fallback_kwargs)
        except Exception as e2:
            last_err = e2
            log.warning(f"Bundled Chromium launch failed: {e2}. Retrying with fresh clean fallback profile...")

    # Step 3: Fallback with clean profile directory if profile locked or corrupt
    if not context:
        clean_profile = profile_dir.parent / f"{profile_dir.name}-fallback"
        clean_profile.mkdir(parents=True, exist_ok=True)
        fallback_kwargs = dict(launch_kwargs)
        fallback_kwargs.pop("channel", None)
        fallback_kwargs.pop("executable_path", None)
        fallback_kwargs["user_data_dir"] = str(clean_profile)
        try:
            context = playwright.chromium.launch_persistent_context(**fallback_kwargs)
        except Exception as e3:
            last_err = e3

    if not context:
        raise RuntimeError(f"Could not launch browser context after all fallbacks: {last_err}")

    # Inject stealth evasion scripts across all frames and pages
    try:
        context.add_init_script(STEALTH_INIT_SCRIPT)
    except Exception as init_ex:
        log.warning(f"Could not register stealth init script: {init_ex}")

    # Single active tab focused directly on VFS portal
    if context.pages:
        vfs_page = context.pages[0]
    else:
        vfs_page = context.new_page()

    # Apply tracking script blocker for high-speed page loads
    setup_performance_routes(vfs_page)

    if not actual_headless:
        try:
            vfs_page.bring_to_front()
        except Exception:
            pass
        if sys.platform == "win32":
            time.sleep(0.5)
            bring_browser_to_front(vfs_page)

    return context, vfs_page
