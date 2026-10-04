# VFS Global Bulgaria Appointment Automation

A high-performance, 100% Python automation assistant with a modern real-time Web Control Dashboard and direct CLI runner. Designed to run seamlessly on your local desktop (with visible browser interaction) or deployed to a cloud VPS (Ubuntu/Debian Linux).

---

## 🎯 Target Workflow Covered

1. **Open Portal**: Opens `https://visa.vfsglobal.com/ind/en/bgr/book-an-appointment`.
2. **Dismiss Overlays**: Automatically clears OneTrust cookie consent banners.
3. **Click "Book now"**: Locates and clicks the primary *Book now* button.
4. **Login**: Navigates to `https://visa.vfsglobal.com/ind/en/bgr/login` and enters your credentials:
   - Email: `oli930110@gmail.com`
   - Password: `Milan@123`
5. **Cloudflare & Sign In**: Interacts with Cloudflare Turnstile, awaits token verification, and clicks *Sign In*.
6. **Gmail OTP Auto-Retrieval**: Securely connects to `imap.gmail.com:993` via IMAP, polls for the incoming verification code, extracts the 6-digit OTP, fills the input fields, and submits.
7. **Dashboard & Start New Booking**: Detects arrival at `https://visa.vfsglobal.com/ind/en/bgr/dashboard` and clicks the orange *Start New Booking* button.
8. **Appointment Details**: Lands on `https://visa.vfsglobal.com/ind/en/bgr/application-detail` and holds the browser session open and active for the next form inputs.

---

## 🚀 Quick Start (Local Windows)

### 1. Requirements
Ensure Python 3.10+ and Playwright Chromium are installed:
```powershell
pip install -r requirements.txt
playwright install chromium
```

### 2. Launch the Web Control Dashboard
Run the Flask server:
```powershell
python app.py
```
Open your browser at: **[http://127.0.0.1:5000](http://127.0.0.1:5000)**

Click the **Start Automation** button. The browser window will open right in front of you and automatically carry out the login and booking journey.

### 3. Direct CLI Runner (Optional)
If you prefer running directly in your terminal without the web interface:
```powershell
python run.py
```

---

## 🌐 VPS Hosting Deployment Guide (Linux)

You can easily host this automation on any Linux VPS (Ubuntu 22.04 / 24.04, Debian):

### 1. System Setup
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv xvfb libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2
```

### 2. Clone / Upload Project & Install Dependencies
```bash
cd /opt/vfs-automation
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 3. Running with Virtual Display (Xvfb)
For VPS instances without a physical monitor, run with Xvfb so the browser operates in full headed mode (avoiding bot-detection flags):
```bash
xvfb-run --auto-servernum --server-args="-screen 0 1440x900x24" python3 app.py
```

The Web Dashboard will be accessible on `http://YOUR_VPS_IP:5000`.

---

## 🔒 Configuration (`.env`)

```ini
VFS_EMAIL=oli930110@gmail.com
VFS_PASSWORD=Milan@123

TARGET_CITY=delhi
VISA_CATEGORY=Business
VISA_SUB_CATEGORY=Business Visa
PORTAL_URL=https://visa.vfsglobal.com/ind/en/bgr

BROWSER_CHANNEL=brave
HEADLESS=false
DASHBOARD_PORT=4140
```
