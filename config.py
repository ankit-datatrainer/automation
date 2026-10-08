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
    # Dynamic VFS Portal Credentials (Stored securely in Database, NOT in .env)
    _vfs_email: str | None = None
    _vfs_password: str | None = None
    _vfs_gmail_user: str | None = None
    _vfs_gmail_app_password: str | None = None

    @property
    def VFS_EMAIL(self) -> str:
        if self._vfs_email:
            return self._vfs_email
        try:
            import vfs_db
            acc = vfs_db.get_active_vfs_account()
            if acc and acc.get("vfs_email"):
                return acc["vfs_email"]
        except Exception:
            pass
        return os.getenv("VFS_EMAIL", "oli930110@gmail.com")

    @VFS_EMAIL.setter
    def VFS_EMAIL(self, val: str):
        self._vfs_email = str(val).strip()

    @property
    def VFS_PASSWORD(self) -> str:
        if self._vfs_password:
            return self._vfs_password
        try:
            import vfs_db
            acc = vfs_db.get_active_vfs_account()
            if acc and acc.get("vfs_password"):
                return acc["vfs_password"]
        except Exception:
            pass
        return os.getenv("VFS_PASSWORD", "Milan@123")

    @VFS_PASSWORD.setter
    def VFS_PASSWORD(self, val: str):
        self._vfs_password = str(val).strip()

    @property
    def VFS_GMAIL_USER(self) -> str:
        if self._vfs_gmail_user:
            return self._vfs_gmail_user
        try:
            import vfs_db
            acc = vfs_db.get_active_vfs_account()
            if acc and acc.get("gmail_user"):
                return acc["gmail_user"]
        except Exception:
            pass
        return os.getenv("VFS_GMAIL_USER", "mytutorankit@gmail.com")

    @VFS_GMAIL_USER.setter
    def VFS_GMAIL_USER(self, val: str):
        self._vfs_gmail_user = str(val).strip()

    @property
    def VFS_GMAIL_APP_PASSWORD(self) -> str:
        if self._vfs_gmail_app_password:
            return self._vfs_gmail_app_password
        try:
            import vfs_db
            acc = vfs_db.get_active_vfs_account()
            if acc and acc.get("gmail_app_password"):
                return acc["gmail_app_password"]
        except Exception:
            pass
        return os.getenv("VFS_GMAIL_APP_PASSWORD", "hwzw lrzx xovu ybgs")

    @VFS_GMAIL_APP_PASSWORD.setter
    def VFS_GMAIL_APP_PASSWORD(self, val: str):
        self._vfs_gmail_app_password = str(val).strip()

    # Dynamic Target Route (Stored in DB / applicant profile, NOT in .env)
    _target_city: str | None = None
    _visa_category: str | None = None
    _visa_sub_category: str | None = None

    @property
    def TARGET_CITY(self) -> str:
        if self._target_city:
            return self._target_city
        try:
            import vfs_db
            app_prof = vfs_db.get_active_applicant()
            if app_prof and app_prof.get("target_city"):
                return str(app_prof["target_city"]).lower()
        except Exception:
            pass
        return os.getenv("TARGET_CITY", "delhi").lower()

    @TARGET_CITY.setter
    def TARGET_CITY(self, val: str):
        self._target_city = str(val).strip().lower()

    @property
    def VISA_CATEGORY(self) -> str:
        if self._visa_category:
            return self._visa_category
        try:
            import vfs_db
            app_prof = vfs_db.get_active_applicant()
            if app_prof and app_prof.get("visa_category"):
                return str(app_prof["visa_category"])
        except Exception:
            pass
        return os.getenv("VISA_CATEGORY", "Long Stay D visa")

    @VISA_CATEGORY.setter
    def VISA_CATEGORY(self, val: str):
        self._visa_category = str(val).strip()

    @property
    def VISA_SUB_CATEGORY(self) -> str:
        if self._visa_sub_category:
            return self._visa_sub_category
        try:
            import vfs_db
            app_prof = vfs_db.get_active_applicant()
            if app_prof and app_prof.get("visa_sub_category"):
                return str(app_prof["visa_sub_category"])
        except Exception:
            pass
        return os.getenv("VISA_SUB_CATEGORY", "Long Stay D visa")

    @VISA_SUB_CATEGORY.setter
    def VISA_SUB_CATEGORY(self, val: str):
        self._visa_sub_category = str(val).strip()

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

    # Telegram 24/7 Slot Notifications (Stored in DB slot_monitor_settings, NOT in .env)
    _telegram_bot_token: str | None = None
    _telegram_chat_id: str | None = None

    @property
    def TELEGRAM_BOT_TOKEN(self) -> str:
        if self._telegram_bot_token:
            return self._telegram_bot_token
        try:
            import vfs_db
            sms = vfs_db.get_slot_monitor_settings()
            if sms and sms.get("telegram_bot_token"):
                return str(sms["telegram_bot_token"])
        except Exception:
            pass
        return os.getenv("TELEGRAM_BOT_TOKEN", "8954641441:AAFb94KC7eQtBXtVmkyGZDYA9eixHsUUzrw")

    @TELEGRAM_BOT_TOKEN.setter
    def TELEGRAM_BOT_TOKEN(self, val: str):
        self._telegram_bot_token = str(val).strip()

    @property
    def TELEGRAM_CHAT_ID(self) -> str:
        if self._telegram_chat_id:
            return self._telegram_chat_id
        try:
            import vfs_db
            sms = vfs_db.get_slot_monitor_settings()
            if sms and sms.get("telegram_chat_id"):
                return str(sms["telegram_chat_id"])
        except Exception:
            pass
        return os.getenv("TELEGRAM_CHAT_ID", "7815919062")

    @TELEGRAM_CHAT_ID.setter
    def TELEGRAM_CHAT_ID(self, val: str):
        self._telegram_chat_id = str(val).strip()

    # Remote MySQL (Hostinger srv2203.hstgr.io / 82.25.121.184)
    DB_HOST: str = os.getenv("DB_HOST", "srv2203.hstgr.io")
    DB_PORT: int = int(os.getenv("DB_PORT", "3306"))
    DB_DATABASE: str = os.getenv("DB_DATABASE", "")
    DB_USERNAME: str = os.getenv("DB_USERNAME", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_ENABLED: bool = os.getenv("DB_ENABLED", "false").lower() in ("true", "1", "yes")

    # Dynamic Applicant details (Primary) - Loaded from DB / applicants.json, NEVER stored in .env
    _applicant_data_mem: dict | None = None

    def _get_applicant_val(self, key: str, default: str) -> str:
        if self._applicant_data_mem and key in self._applicant_data_mem:
            return str(self._applicant_data_mem[key])
        try:
            import vfs_db
            prof = vfs_db.get_active_applicant()
            if prof and prof.get(key):
                return str(prof[key])
        except Exception:
            pass
        return default

    @property
    def APPLICANT_FIRST_NAME(self) -> str:
        return self._get_applicant_val("first_name", "MILAN")

    @APPLICANT_FIRST_NAME.setter
    def APPLICANT_FIRST_NAME(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["first_name"] = str(val).strip()

    @property
    def APPLICANT_LAST_NAME(self) -> str:
        return self._get_applicant_val("last_name", "RINJALI MAGAR")

    @APPLICANT_LAST_NAME.setter
    def APPLICANT_LAST_NAME(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["last_name"] = str(val).strip()

    @property
    def APPLICANT_GENDER(self) -> str:
        return self._get_applicant_val("gender", "Male")

    @APPLICANT_GENDER.setter
    def APPLICANT_GENDER(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["gender"] = str(val).strip()

    @property
    def APPLICANT_DOB(self) -> str:
        return self._get_applicant_val("dob", "30/04/2003")

    @APPLICANT_DOB.setter
    def APPLICANT_DOB(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["dob"] = str(val).strip()

    @property
    def APPLICANT_NATIONALITY(self) -> str:
        return self._get_applicant_val("nationality", "NEPAL")

    @APPLICANT_NATIONALITY.setter
    def APPLICANT_NATIONALITY(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["nationality"] = str(val).strip()

    @property
    def APPLICANT_PASSPORT_NUMBER(self) -> str:
        return self._get_applicant_val("passport_number", "PA0273677")

    @APPLICANT_PASSPORT_NUMBER.setter
    def APPLICANT_PASSPORT_NUMBER(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["passport_number"] = str(val).strip()

    @property
    def APPLICANT_PASSPORT_EXPIRY(self) -> str:
        return self._get_applicant_val("passport_expiry", "12/04/2032")

    @APPLICANT_PASSPORT_EXPIRY.setter
    def APPLICANT_PASSPORT_EXPIRY(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["passport_expiry"] = str(val).strip()

    @property
    def APPLICANT_PHONE_CODE(self) -> str:
        return self._get_applicant_val("phone_code", "977")

    @APPLICANT_PHONE_CODE.setter
    def APPLICANT_PHONE_CODE(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["phone_code"] = str(val).strip()

    @property
    def APPLICANT_PHONE(self) -> str:
        return self._get_applicant_val("phone", "7838349247")

    @APPLICANT_PHONE.setter
    def APPLICANT_PHONE(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["phone"] = str(val).strip()

    @property
    def APPLICANT_EMAIL(self) -> str:
        return self._get_applicant_val("email", "oli930110@gmail.com")

    @APPLICANT_EMAIL.setter
    def APPLICANT_EMAIL(self, val: str):
        if not self._applicant_data_mem:
            self._applicant_data_mem = {}
        self._applicant_data_mem["email"] = str(val).strip()

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
            "phone_code": self.APPLICANT_PHONE_CODE,
            "phone": self.APPLICANT_PHONE,
            "email": self.APPLICANT_EMAIL,
        }

    def load_applicants(self) -> list:
        """Load applicants list from DB or data/applicants.json fallback."""
        try:
            import vfs_db
            profiles = vfs_db.get_applicant_profiles()
            if profiles and len(profiles) > 0:
                clean_list = []
                for p in profiles[:5]:
                    clean_list.append({
                        "id": p.get("id"),
                        "first_name": p.get("first_name", ""),
                        "last_name": p.get("last_name", ""),
                        "gender": p.get("gender", "Male"),
                        "dob": p.get("dob", ""),
                        "nationality": p.get("nationality", "INDIA"),
                        "passport_number": p.get("passport_number", ""),
                        "passport_expiry": p.get("passport_expiry", ""),
                        "phone_code": p.get("phone_code", "977"),
                        "phone": p.get("phone", ""),
                        "email": p.get("email", ""),
                        "is_active": p.get("is_active", 1)
                    })
                return clean_list
        except Exception:
            pass

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
                "nationality": str(a.get("nationality", "INDIA")).strip(),
                "passport_number": str(a.get("passport_number", "")).strip(),
                "passport_expiry": str(a.get("passport_expiry", "")).strip(),
                "phone_code": str(a.get("phone_code") or getattr(self, "APPLICANT_PHONE_CODE", "977")).strip() or "977",
                "phone": str(a.get("phone", "")).strip(),
                "email": str(a.get("email", "")).strip(),
            })

        APPLICANTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        APPLICANTS_FILE.write_text(json.dumps(clean_list, indent=2), encoding="utf-8")

        # Sync first applicant with in-memory primary
        if clean_list:
            first = clean_list[0]
            self.APPLICANT_FIRST_NAME = first.get("first_name") or self.APPLICANT_FIRST_NAME
            self.APPLICANT_LAST_NAME = first.get("last_name") or self.APPLICANT_LAST_NAME
            self.APPLICANT_GENDER = first.get("gender") or self.APPLICANT_GENDER
            self.APPLICANT_DOB = first.get("dob") or self.APPLICANT_DOB
            self.APPLICANT_NATIONALITY = first.get("nationality") or self.APPLICANT_NATIONALITY
            self.APPLICANT_PASSPORT_NUMBER = first.get("passport_number") or self.APPLICANT_PASSPORT_NUMBER
            self.APPLICANT_PASSPORT_EXPIRY = first.get("passport_expiry") or self.APPLICANT_PASSPORT_EXPIRY
            self.APPLICANT_PHONE_CODE = first.get("phone_code") or self.APPLICANT_PHONE_CODE
            self.APPLICANT_PHONE = first.get("phone") or self.APPLICANT_PHONE
            self.APPLICANT_EMAIL = first.get("email") or self.APPLICANT_EMAIL

    @property
    def APPLICANTS_LIST(self) -> list:
        return self.load_applicants()

    # ONLY database connection and runtime flags are ever allowed in .env
    ALLOWED_ENV_KEYS = {
        "DB_HOST", "DB_PORT", "DB_DATABASE", "DB_USERNAME", "DB_PASSWORD", "DB_ENABLED",
        "DASHBOARD_PORT", "BROWSER_CHANNEL", "HEADLESS"
    }

    SENSITIVE_CREDENTIAL_KEYS = {
        "VFS_EMAIL", "VFS_PASSWORD", "VFS_GMAIL_USER", "VFS_GMAIL_APP_PASSWORD"
    }

    def update_config(self, updates: dict):
        """Update in-memory config, database, and strictly persist ONLY db/runtime keys to .env."""
        db_account_updates = {}
        for k, val in updates.items():
            if k in self.SENSITIVE_CREDENTIAL_KEYS:
                setattr(self, k, str(val))
                if k == "VFS_EMAIL":
                    db_account_updates["vfs_email"] = str(val)
                elif k == "VFS_PASSWORD":
                    db_account_updates["vfs_password"] = str(val)
                elif k == "VFS_GMAIL_USER":
                    db_account_updates["gmail_user"] = str(val)
                elif k == "VFS_GMAIL_APP_PASSWORD":
                    db_account_updates["gmail_app_password"] = str(val)
            elif hasattr(self, k):
                orig_type = type(getattr(self, k, ""))
                if orig_type == bool:
                    setattr(self, k, str(val).lower() in ("true", "1", "yes"))
                elif orig_type == int:
                    setattr(self, k, int(val))
                else:
                    setattr(self, k, val)

        # Synchronize credentials to active database VFS account if changed
        if db_account_updates:
            try:
                import vfs_db
                active_acc = vfs_db.get_active_vfs_account()
                if active_acc:
                    db_account_updates["id"] = active_acc["id"]
                    db_account_updates.setdefault("account_name", active_acc.get("account_name"))
                    db_account_updates.setdefault("vfs_email", active_acc.get("vfs_email"))
                    db_account_updates.setdefault("vfs_password", active_acc.get("vfs_password"))
                    db_account_updates.setdefault("gmail_user", active_acc.get("gmail_user"))
                    db_account_updates.setdefault("gmail_app_password", active_acc.get("gmail_app_password"))
                    vfs_db.save_vfs_account(db_account_updates, is_admin=True)
            except Exception:
                pass

        # Update slot monitor settings in DB if Telegram keys updated
        if "TELEGRAM_BOT_TOKEN" in updates or "TELEGRAM_CHAT_ID" in updates:
            try:
                import vfs_db
                vfs_db.update_slot_monitor_settings({
                    "telegram_bot_token": self.TELEGRAM_BOT_TOKEN,
                    "telegram_chat_id": self.TELEGRAM_CHAT_ID
                })
            except Exception:
                pass

        # Read existing .env lines
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
                # Strictly drop any key not in ALLOWED_ENV_KEYS
                if k not in self.ALLOWED_ENV_KEYS:
                    continue
                existing_keys.add(k)
                if k in updates and k in self.ALLOWED_ENV_KEYS:
                    val = str(updates[k])
                    new_lines.append(f"{k}={val}")
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # Append new allowed keys if any
        for k, v in updates.items():
            if k in self.ALLOWED_ENV_KEYS and k not in existing_keys:
                val = str(v)
                new_lines.append(f"{k}={val}")

        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        load_dotenv(dotenv_path=ENV_PATH, override=True)


cfg = Config()
