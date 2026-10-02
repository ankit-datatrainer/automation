"""Configuration loader for VFS Global Automation."""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from current directory
ENV_PATH = Path(__file__).resolve().parent / ".env"
APPLICANTS_FILE = Path(__file__).resolve().parent / "data" / "applicants.json"
load_dotenv(dotenv_path=ENV_PATH, override=True)


class Config:
    # VFS Account
    VFS_EMAIL: str = os.getenv("VFS_EMAIL", "ankit.developer2004@gmail.com")
    VFS_PASSWORD: str = os.getenv("VFS_PASSWORD", "@Nkit55555")

    # Gmail OTP Retrieval
    VFS_GMAIL_USER: str = os.getenv("VFS_GMAIL_USER", "ankit.developer2004@gmail.com")
    VFS_GMAIL_APP_PASSWORD: str = os.getenv("VFS_GMAIL_APP_PASSWORD", "dbfq cwtw nfwm ouuk")

    # Target Route
    TARGET_CITY: str = os.getenv("TARGET_CITY", "delhi").lower()
    VISA_CATEGORY: str = os.getenv("VISA_CATEGORY", "Business")
    VISA_SUB_CATEGORY: str = os.getenv("VISA_SUB_CATEGORY", "Business Visa")
    PORTAL_URL: str = os.getenv("PORTAL_URL", "https://visa.vfsglobal.com/ind/en/bgr").rstrip("/")

    # Derived URLs
    @property
    def BOOK_APPOINTMENT_URL(self) -> str:
        return f"{self.PORTAL_URL}/book-an-appointment"

    @property
    def LOGIN_URL(self) -> str:
        return f"{self.PORTAL_URL}/login"

    @property
    def DASHBOARD_URL(self) -> str:
        return f"{self.PORTAL_URL}/dashboard"

    @property
    def APPLICATION_DETAIL_URL(self) -> str:
        return f"{self.PORTAL_URL}/application-detail"

    # Browser & System
    BROWSER_CHANNEL: str = os.getenv("BROWSER_CHANNEL", "brave").lower()
    HEADLESS: bool = os.getenv("HEADLESS", "false").lower() in ("true", "1", "yes")
    DASHBOARD_PORT: int = int(os.getenv("DASHBOARD_PORT", "4140"))
    ACTION_TIMEOUT_MS: int = 35000
    OTP_TIMEOUT_SECONDS: int = 120

    # Telegram 24/7 Slot Notifications
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "8954641441:AAFb94KC7eQtBXtVmkyGZDYA9eixHsUUzrw")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "7815919062")

    # Remote MySQL (Hostinger srv2203.hstgr.io / 82.25.121.184)
    DB_HOST: str = os.getenv("DB_HOST", "srv2203.hstgr.io")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_DATABASE: str = os.getenv("DB_DATABASE", "")
    DB_USERNAME: str = os.getenv("DB_USERNAME", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_ENABLED: bool = os.getenv("DB_ENABLED", "false").lower() in ("true", "1", "yes")

    # Applicant details (Primary)
    APPLICANT_FIRST_NAME: str = os.getenv("APPLICANT_FIRST_NAME", "Ankit")
    APPLICANT_LAST_NAME: str = os.getenv("APPLICANT_LAST_NAME", "Sharma")
    APPLICANT_GENDER: str = os.getenv("APPLICANT_GENDER", "Male")
    APPLICANT_DOB: str = os.getenv("APPLICANT_DOB", "15/06/1995")
    APPLICANT_NATIONALITY: str = os.getenv("APPLICANT_NATIONALITY", "India")
    APPLICANT_PASSPORT_NUMBER: str = os.getenv("APPLICANT_PASSPORT_NUMBER", "Z1234567")
    APPLICANT_PASSPORT_EXPIRY: str = os.getenv("APPLICANT_PASSPORT_EXPIRY", "20/05/2031")
    APPLICANT_PHONE: str = os.getenv("APPLICANT_PHONE", "7838349247")
    APPLICANT_EMAIL: str = os.getenv("APPLICANT_EMAIL", "ankit.developer2004@gmail.com")

    @property
    def APPLICANT_DATA(self) -> dict:
        return {
            "first_name": self.APPLICANT_FIRST_NAME,
            "last_name": self.APPLICANT_LAST_NAME,
            "gender": self.APPLICANT_GENDER,
            "dob": self.APPLICANT_DOB,
            "nationality": self.APPLICANT_NATIONALITY,
            "passport_number": self.APPLICANT_PASSPORT_NUMBER,
            "passport_expiry": self.APPLICANT_PASSPORT_EXPIRY,
            "phone": self.APPLICANT_PHONE,
            "email": self.APPLICANT_EMAIL,
        }

    def load_applicants(self) -> list:
        """Load applicants list from data/applicants.json or fallback to primary."""
        APPLICANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if APPLICANTS_FILE.exists():
            try:
                data = json.loads(APPLICANTS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    return data[:5]
            except Exception:
                pass
        return [self.APPLICANT_DATA]

    def save_applicants(self, applicants: list):
        """Save applicants list (up to 5 applicants) to data/applicants.json and sync primary."""
        if not applicants:
            applicants = [self.APPLICANT_DATA]
        clean_list = []
        for a in applicants[:5]:
            clean_list.append({
                "first_name": str(a.get("first_name", "")).strip(),
                "last_name": str(a.get("last_name", "")).strip(),
                "gender": str(a.get("gender", "Male")).strip(),
                "dob": str(a.get("dob", "")).strip(),
                "nationality": str(a.get("nationality", "India")).strip(),
                "passport_number": str(a.get("passport_number", "")).strip(),
                "passport_expiry": str(a.get("passport_expiry", "")).strip(),
                "phone": str(a.get("phone", "")).strip(),
                "email": str(a.get("email", "")).strip(),
            })

        APPLICANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        APPLICANTS_FILE.write_text(json.dumps(clean_list, indent=2), encoding="utf-8")

        # Sync first applicant with primary environment config
        if clean_list:
            first = clean_list[0]
            self.update_config({
                "APPLICANT_FIRST_NAME": first.get("first_name") or self.APPLICANT_FIRST_NAME,
                "APPLICANT_LAST_NAME": first.get("last_name") or self.APPLICANT_LAST_NAME,
                "APPLICANT_GENDER": first.get("gender") or self.APPLICANT_GENDER,
                "APPLICANT_DOB": first.get("dob") or self.APPLICANT_DOB,
                "APPLICANT_NATIONALITY": first.get("nationality") or self.APPLICANT_NATIONALITY,
                "APPLICANT_PASSPORT_NUMBER": first.get("passport_number") or self.APPLICANT_PASSPORT_NUMBER,
                "APPLICANT_PASSPORT_EXPIRY": first.get("passport_expiry") or self.APPLICANT_PASSPORT_EXPIRY,
                "APPLICANT_PHONE": first.get("phone") or self.APPLICANT_PHONE,
                "APPLICANT_EMAIL": first.get("email") or self.APPLICANT_EMAIL,
            })

    @property
    def APPLICANTS_LIST(self) -> list:
        return self.load_applicants()


    def update_config(self, updates: dict):
        """Update in-memory config and persist changes to .env file."""
        lines = []
        if ENV_PATH.exists():
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

        existing_keys = set()
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                k = k.strip()
                existing_keys.add(k)
                if k in updates:
                    val = str(updates[k])
                    new_lines.append(f"{k}={val}")
                    if hasattr(self, k):
                        orig_type = type(getattr(self, k))
                        if orig_type == bool:
                            setattr(self, k, val.lower() in ("true", "1", "yes"))
                        elif orig_type == int:
                            setattr(self, k, int(val))
                        else:
                            setattr(self, k, val)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        for k, v in updates.items():
            if k not in existing_keys:
                val = str(v)
                new_lines.append(f"{k}={val}")
                if hasattr(self, k):
                    orig_type = type(getattr(self, k))
                    if orig_type == bool:
                        setattr(self, k, val.lower() in ("true", "1", "yes"))
                    elif orig_type == int:
                        setattr(self, k, int(val))
                    else:
                        setattr(self, k, val)

        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        load_dotenv(dotenv_path=ENV_PATH, override=True)


cfg = Config()
