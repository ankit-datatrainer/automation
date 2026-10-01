# VFS Global Bulgaria Appointment Automation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a robust, 100% Python-based automation with a modern web interface that opens VFS Global Bulgaria, navigates to login, inputs credentials, extracts the OTP from Gmail via IMAP, logs in, clicks "Start New Booking", and holds the browser open on the Appointment Details form.

**Architecture:** A lightweight Flask server serves a responsive, dark-mode real-time dashboard allowing the operator to start/stop the automation and observe streaming logs. The backend leverages Playwright with stealth configurations to launch a visible browser window, execute resilient DOM navigation through the VFS Angular lifecycle, fetch the OTP via a dedicated IMAP client, and maintain an active session on `/application-detail`.

**Tech Stack:** Python 3.14+, Flask (Web UI & REST API), Playwright (Stealth Browser Automation), `imaplib` + `email` (Gmail OTP Retrieval), `python-dotenv` (Configuration), HTML5/Tailwind/Vanilla CSS (Frontend Dashboard).

---

### Task 1: Environment & Configuration Setup

**Files:**
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/.env`
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/.env.example`
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/requirements.txt`
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/config.py`
- Test: `c:/Users/ankit/OneDrive/Desktop/Automation/tests/test_config.py`

**Step 1: Write the configuration test**
Verify that environment variables are loaded and typed correctly with fallback defaults.

**Step 2: Implement `config.py` and `.env`**
Load `VFS_EMAIL`, `VFS_PASSWORD`, `VFS_GMAIL_USER`, `VFS_GMAIL_APP_PASSWORD`, `TARGET_CITY`, `VISA_CATEGORY`, `PORT`, and `HEADLESS` flags.

**Step 3: Run test to verify**
Execute `pytest tests/test_config.py` to ensure all configurations load properly.

---

### Task 2: Automated Gmail OTP Retrieval Engine

**Files:**
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/vfs_otp.py`
- Test: `c:/Users/ankit/OneDrive/Desktop/Automation/tests/test_otp.py`

**Step 1: Write test for OTP parser and IMAP connectivity**
Verify regex extraction of 6-digit verification code from typical VFS Global email bodies and subject lines. Test SSL connection to `imap.gmail.com:993` with provided credentials.

**Step 2: Implement `vfs_otp.py`**
- `get_inbox_baseline()`: Records the current max email UID or timestamp before clicking Sign In to avoid picking up old OTPs.
- `fetch_latest_otp(timeout_seconds=90, poll_interval=3, baseline_time=None)`: Connects via IMAP4_SSL, searches the inbox for messages from VFS / containing OTP keywords after the baseline, extracts the 6-digit code, and returns it.

**Step 3: Run test to verify**
Run `pytest tests/test_otp.py` to verify regex matching and live IMAP mailbox authentication.

---

### Task 3: Resilient Browser Automation Engine (Playwright)

**Files:**
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/vfs_browser.py`
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/vfs_automation.py`
- Test: `c:/Users/ankit/OneDrive/Desktop/Automation/tests/test_browser_init.py`

**Step 1: Implement `vfs_browser.py`**
- Browser launcher supporting visible mode (headed) for both Windows desktop (auto-detecting Brave / Chrome / Chromium) and headless/virtual display for Linux VPS.
- Anti-bot stealth arguments: `--disable-blink-features=AutomationControlled`, randomized realistic user-agent, viewport maximization, removal of `navigator.webdriver`.

**Step 2: Implement `vfs_automation.py` Navigation Flow**
1. Navigate to `https://visa.vfsglobal.com/ind/en/bgr/book-an-appointment`.
2. Dismiss OneTrust cookie overlay if present.
3. Locate and click "Book now" button.
4. Detect redirect to `https://visa.vfsglobal.com/ind/en/bgr/login`.
5. Enter credentials (`ankit.developer2004@gmail.com` / `@Nkit55555`) with natural delays and Angular dispatch events.
6. Check for Cloudflare Turnstile token or checkbox; interact if needed.
7. Click "Sign In" button.
8. Detect OTP screen, trigger `fetch_latest_otp()`, fill OTP inputs, and click Submit.
9. Detect transition to `https://visa.vfsglobal.com/ind/en/bgr/dashboard`.
10. Find and click "Start New Booking" button.
11. Wait for `https://visa.vfsglobal.com/ind/en/bgr/application-detail` and enter hold state (`APPOINTMENT_DETAILS_ACTIVE`).

---

### Task 4: Real-time Web Control Dashboard (Flask)

**Files:**
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/app.py`
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/templates/index.html`
- Create: `c:/Users/ankit/OneDrive/Desktop/Automation/static/css/style.css`

**Step 1: Implement Flask Backend (`app.py`)**
- Endpoints:
  - `GET /`: Serves the control dashboard.
  - `POST /api/start`: Starts the automation thread with chosen parameters.
  - `POST /api/stop`: Stops/terminates the active automation.
  - `GET /api/status`: Returns current step, status (IDLE, RUNNING, COMPLETED, ERROR), and latest logs.
  - `GET /api/logs/stream`: SSE (Server-Sent Events) endpoint for live streaming terminal output.

**Step 2: Implement UI (`templates/index.html` + `static/css/style.css`)**
- Premium dark-theme interface with:
  - Automation status banner (with pulsing status indicator).
  - Target route details (India -> Bulgaria Type D / Delhi).
  - Start Automation button & Stop button.
  - Live Step Progress Tracker (Book Now -> Login -> Cloudflare/OTP -> Dashboard -> Application Details).
  - Live Console log window streaming real-time status.

---

### Task 5: End-to-End Validation & Verification

**Files:**
- Test: Run live dry-run / integration test with browser launched visibly.
- Verify:
  1. Web UI opens at `http://127.0.0.1:5000`.
  2. Clicking "Start Automation" opens visible Chrome/Brave window.
  3. Navigates to `book-an-appointment` -> clicks "Book now".
  4. Enters credentials on `login`.
  5. Solves/awaits Turnstile and submits Sign In.
  6. Fetches OTP from Gmail via IMAP and fills it.
  7. Clicks "Start New Booking" on dashboard.
  8. Confirms landing on `application-detail` form and holds session open.
