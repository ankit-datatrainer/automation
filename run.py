"""Main Entrypoint for VFS Global Bulgaria Appointment Automation Suite.

By default, launches the Web Control Suite and automatically opens the UI dashboard in your browser.
Pass `--cli` flag to run in direct console mode.
"""

import logging
import os
import sys

# Ensure UTF-8 stdout encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("vfs.main")


def run_cli():
    from vfs_automation import VFSAutomation
    print("\n=======================================================")
    print("VFS GLOBAL APPOINTMENT AUTOMATION (DIRECT RUNNER)")
    print(f"Target:    India -> Bulgaria ({cfg.TARGET_CITY.upper()})")
    print(f"Account:   {cfg.VFS_EMAIL}")
    print(f"Browser:   {cfg.BROWSER_CHANNEL} (Headless: {cfg.HEADLESS})")
    print("=======================================================\n")

    def console_cb(step, msg):
        print(f"[{step}] {msg}")

    automation = VFSAutomation(config=cfg, log_cb=console_cb)
    try:
        automation.run()
    except KeyboardInterrupt:
        print("\n[STOPPED] Session interrupted by user.")
        automation.stop_requested = True
    except Exception as e:
        print(f"\n[ERROR] Automation halted: {e}")
        sys.exit(1)


def main():
    if "--cli" in sys.argv:
        run_cli()
    else:
        # Default: Launch Web Control Suite and open UI in browser
        import app
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", cfg.DASHBOARD_PORT))
        local_url = f"http://localhost:{port}"

        print("\n=======================================================")
        print("VFS GLOBAL BOOKING AUTOMATION — CONTROL SUITE")
        print(f"Opening Interface in Browser: {local_url}")
        print(f"Network / VPS URL:            http://{host}:{port}")
        print("=======================================================\n")

        if not os.getenv("NO_AUTO_OPEN"):
            import threading
            threading.Thread(target=app.auto_open_browser, args=(local_url,), daemon=True).start()

        app.app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
