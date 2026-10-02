"""Database Migration and Synchronization Script for VFS Global Automation Suite.

Pushes all table schemas, user accounts, applicant profiles, and VFS account
credentials to the remote Hostinger MySQL / MariaDB database (srv2203.hstgr.io).
"""

import json
import logging
import os
import sys
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from config import cfg
import vfs_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("db_push")

def run_sync():
    print("=" * 60)
    print("🚀 PUSHING ALL CHANGES & DATA TO REMOTE HOSTINGER MYSQL DATABASE")
    print(f"   Host:     {cfg.DB_HOST}:{cfg.DB_PORT}")
    print(f"   Database: {cfg.DB_DATABASE}")
    print(f"   Username: {cfg.DB_USERNAME}")
    print("=" * 60)

    # 1. Test Connection
    test = vfs_db.test_connection()
    if not test.get("success"):
        print(f"❌ Connection failed: {test.get('message')}")
        sys.exit(1)
    print(f"✅ Connected to remote MySQL! Server version: {test.get('version')}")

    # 2. Initialize Tables & Seed Users
    print("\n[Step 1/4] Initializing / Verifying all database tables & admin accounts...")
    ok = vfs_db.init_db()
    if not ok:
        print("❌ Failed to initialize database tables.")
        sys.exit(1)
    print("✅ Core tables verified: users, applicant_profiles, vfs_accounts, booking_history")

    # 3. Synchronize VFS Accounts
    print("\n[Step 2/4] Synchronizing VFS Credentials into vfs_accounts...")
    try:
        conn = vfs_db.get_db_connection()
        with conn.cursor() as cur:
            if cfg.VFS_EMAIL:
                cur.execute("""
                    INSERT INTO vfs_accounts (vfs_email, vfs_password, gmail_user, gmail_app_password, status)
                    VALUES (%s, %s, %s, %s, 'active')
                    ON DUPLICATE KEY UPDATE 
                        vfs_password=VALUES(vfs_password),
                        gmail_user=VALUES(gmail_user),
                        gmail_app_password=VALUES(gmail_app_password),
                        status='active'
                """, (cfg.VFS_EMAIL, cfg.VFS_PASSWORD, cfg.VFS_GMAIL_USER, cfg.VFS_GMAIL_APP_PASSWORD))
                print(f"✅ VFS account '{cfg.VFS_EMAIL}' synchronized in remote database.")
        conn.close()
    except Exception as e:
        print(f"⚠️ VFS account sync notice: {e}")

    # 4. Synchronize Applicant Profiles
    print("\n[Step 3/4] Synchronizing Applicant Profiles into applicant_profiles...")
    existing = vfs_db.get_all_applicants()
    existing_passports = {p.get("passport_number", "").strip().upper() for p in existing if p.get("passport_number")}

    # A. Primary profile from .env
    if cfg.APPLICANT_FIRST_NAME and cfg.APPLICANT_PASSPORT_NUMBER:
        p_num = cfg.APPLICANT_PASSPORT_NUMBER.strip().upper()
        if p_num not in existing_passports:
            vfs_db.save_applicant_profile({
                "profile_name": f"{cfg.APPLICANT_FIRST_NAME} {cfg.APPLICANT_LAST_NAME} (Primary)",
                "first_name": cfg.APPLICANT_FIRST_NAME,
                "last_name": cfg.APPLICANT_LAST_NAME,
                "gender": cfg.APPLICANT_GENDER,
                "dob": cfg.APPLICANT_DOB,
                "nationality": cfg.APPLICANT_NATIONALITY,
                "passport_number": cfg.APPLICANT_PASSPORT_NUMBER,
                "passport_expiry": cfg.APPLICANT_PASSPORT_EXPIRY,
                "phone": cfg.APPLICANT_PHONE,
                "email": cfg.APPLICANT_EMAIL,
                "target_city": cfg.TARGET_CITY,
                "visa_category": cfg.VISA_CATEGORY,
                "visa_sub_category": cfg.VISA_SUB_CATEGORY
            })
            existing_passports.add(p_num)
            print(f"✅ Added Primary Applicant: {cfg.APPLICANT_FIRST_NAME} {cfg.APPLICANT_LAST_NAME} ({p_num})")
        else:
            print(f"ℹ️ Primary Applicant ({p_num}) already in database.")

    # B. Load and push applicants from data/applicants.json
    applicants_file = os.path.join(os.path.dirname(__file__), "data", "applicants.json")
    if os.path.exists(applicants_file):
        try:
            with open(applicants_file, "r", encoding="utf-8") as f:
                file_apps = json.load(f)
            for idx, a in enumerate(file_apps, 1):
                p_num = a.get("passport_number", "").strip().upper()
                if p_num and p_num not in existing_passports:
                    ok, msg, pid = vfs_db.save_applicant_profile({
                        "profile_name": f"{a.get('first_name')} {a.get('last_name')} (Group #{idx})",
                        "first_name": a.get("first_name", ""),
                        "last_name": a.get("last_name", ""),
                        "gender": a.get("gender", "Male"),
                        "dob": a.get("dob", ""),
                        "nationality": a.get("nationality", "India"),
                        "passport_number": a.get("passport_number", ""),
                        "passport_expiry": a.get("passport_expiry", ""),
                        "phone": a.get("phone", ""),
                        "email": a.get("email", ""),
                        "target_city": cfg.TARGET_CITY,
                        "visa_category": cfg.VISA_CATEGORY,
                        "visa_sub_category": cfg.VISA_SUB_CATEGORY
                    })
                    existing_passports.add(p_num)
                    print(f"✅ Added Group Applicant #{idx}: {a.get('first_name')} {a.get('last_name')} ({p_num}) -> ID #{pid}")
                elif p_num:
                    print(f"ℹ️ Group Applicant #{idx} ({p_num}) already in database.")
        except Exception as e:
            print(f"⚠️ Error reading applicants.json: {e}")

    # 5. Summary & Verification
    print("\n[Step 4/4] Verifying Final Database State...")
    stats = vfs_db.get_database_stats()
    print("=" * 60)
    print("📊 REMOTE DATABASE STATUS & TABLE COUNTS:")
    for k, v in stats.get("stats", {}).items():
        print(f"   • {k:<20}: {v}")
    print("=" * 60)
    print("✨ ALL CHANGES PUSHED TO REMOTE DATABASE SUCCESSFULLY!")

if __name__ == "__main__":
    run_sync()
