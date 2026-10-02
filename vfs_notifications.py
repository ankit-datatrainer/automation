"""Notification Dispatcher for VFS Global Automation.

Handles instant alerts and daily 10 PM reports via:
1. Telegram Bot API (Direct messages & Photo screenshots)
2. Email via SMTP / Gmail App Password
"""

import email
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import logging
import os
import smtplib
from datetime import datetime
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
import requests

from config import cfg

log = logging.getLogger("vfs.notifications")


# =============================================================================
# TELEGRAM DISPATCHER
# =============================================================================

def send_telegram_message(
    bot_token: str,
    chat_id: str,
    message: str,
    parse_mode: str = "HTML"
) -> Tuple[bool, str]:
    """Send text message to Telegram chat via Bot API."""
    if not bot_token or not chat_id:
        return False, "Telegram Bot Token and Chat ID are required."
    
    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = {
        "chat_id": str(chat_id).strip(),
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": False
    }

    try:
        res = requests.post(url, json=payload, timeout=12)
        data = res.json()
        if data.get("ok"):
            return True, "Telegram message sent successfully."
        err_msg = data.get("description", "Unknown Telegram error")
        log.warning(f"Telegram send failed ({chat_id}): {err_msg}")
        return False, err_msg
    except Exception as e:
        log.error(f"Telegram network error ({chat_id}): {e}")
        return False, str(e)


def send_telegram_photo(
    bot_token: str,
    chat_id: str,
    photo_path: str,
    caption: str = "",
    parse_mode: str = "HTML"
) -> Tuple[bool, str]:
    """Send photo with caption to Telegram chat."""
    if not bot_token or not chat_id:
        return False, "Telegram Bot Token and Chat ID are required."
    
    p = Path(photo_path)
    if not p.exists():
        # Fallback to plain text message
        return send_telegram_message(bot_token, chat_id, caption, parse_mode)

    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendPhoto"
    data = {
        "chat_id": str(chat_id).strip(),
        "caption": caption,
        "parse_mode": parse_mode
    }

    try:
        with open(p, "rb") as f:
            files = {"photo": f}
            res = requests.post(url, data=data, files=files, timeout=18)
        resp_data = res.json()
        if resp_data.get("ok"):
            return True, "Telegram photo sent successfully."
        return False, resp_data.get("description", "Failed to send photo")
    except Exception as e:
        log.error(f"Telegram photo error: {e}")
        return False, str(e)


def test_telegram_connection(bot_token: str, chat_id: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """Test Telegram Bot Token validity and optionally send a test ping to chat_id."""
    if not bot_token:
        return False, "Please enter a Telegram Bot Token.", {}

    url = f"https://api.telegram.org/bot{bot_token.strip()}/getMe"
    try:
        res = requests.get(url, timeout=10)
        data = res.json()
        if not data.get("ok"):
            return False, f"Invalid Bot Token: {data.get('description')}", {}

        bot_info = data.get("result", {})
        bot_name = bot_info.get("first_name") or bot_info.get("username", "Bot")

        if chat_id:
            test_text = (
                f"✅ <b>VFS Global Automation Suite — Alert Test</b>\n\n"
                f"🤖 Connected Bot: <b>@{bot_info.get('username')}</b>\n"
                f"📅 Time: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
                f"⚡ 24/7 Slot Watcher notifications are configured and active!"
            )
            ok, msg = send_telegram_message(bot_token, chat_id, test_text)
            if not ok:
                return False, f"Bot is valid (@{bot_info.get('username')}), but message to Chat ID '{chat_id}' failed: {msg}", bot_info
            return True, f"Connected to @{bot_info.get('username')} and sent test message successfully!", bot_info

        return True, f"Bot token is valid! Connected to @{bot_info.get('username')}.", bot_info
    except Exception as e:
        return False, f"Could not connect to Telegram: {e}", {}


# =============================================================================
# EMAIL DISPATCHER
# =============================================================================

def send_email_alert(
    to_email: str,
    subject: str,
    html_content: str,
    screenshot_path: Optional[str] = None,
    from_user: Optional[str] = None,
    from_password: Optional[str] = None
) -> Tuple[bool, str]:
    """Send rich HTML email alert with optional inline screenshot."""
    if not to_email or "@" not in to_email:
        return False, "A valid recipient email address is required."

    user = from_user if from_user is not None else (cfg.VFS_GMAIL_USER or cfg.VFS_EMAIL)
    pwd = from_password if from_password is not None else cfg.VFS_GMAIL_APP_PASSWORD

    if not user or not pwd:
        return False, "Email sender credentials not configured in .env (VFS_GMAIL_USER & VFS_GMAIL_APP_PASSWORD)."

    clean_pwd = pwd.replace(" ", "")

    msg = MIMEMultipart("related")
    msg["From"] = f"VFS Global Slot Alert <{user}>"
    msg["To"] = to_email.strip()
    msg["Subject"] = subject

    alt = MIMEMultipart("alternative")
    msg.attach(alt)

    # Clean text fallback
    plain_text = html_content.replace("<br>", "\n").replace("</p>", "\n\n")
    plain_text = re.sub(r"<[^>]+>", "", plain_text)
    alt.attach(MIMEText(plain_text, "plain", "utf-8"))
    alt.attach(MIMEText(html_content, "html", "utf-8"))

    # Attach screenshot image if exists
    if screenshot_path and os.path.exists(screenshot_path):
        try:
            with open(screenshot_path, "rb") as img_f:
                img_data = img_f.read()
            mime_img = MIMEImage(img_data)
            mime_img.add_header("Content-ID", "<slot_screenshot>")
            mime_img.add_header("Content-Disposition", "inline", filename="slot_screenshot.png")
            msg.attach(mime_img)
        except Exception as e:
            log.debug(f"Could not attach screenshot to email: {e}")

    try:
        # Connect to Gmail SMTP over SSL (Port 465)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
            server.login(user, clean_pwd)
            server.send_message(msg)
        log.info(f"Alert email sent successfully to {to_email}")
        return True, f"Email sent successfully to {to_email}"
    except Exception as e:
        log.error(f"Email send error to {to_email}: {e}")
        return False, str(e)


def test_email_connection(to_email: str) -> Tuple[bool, str]:
    """Send test email verifying SMTP configuration."""
    subj = "⚡ VFS Global Automation Suite — Alert Test Notification"
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #0f172a; color: #f8fafc; border: 1px solid #334155; border-radius: 12px; padding: 24px;">
      <h2 style="color: #f97316; margin-top: 0;">⚡ VFS Automation Alert System Test</h2>
      <p style="color: #cbd5e1; font-size: 15px;">This is a test notification confirming your email alerting channel is active.</p>
      <div style="background: rgba(15,23,42,0.6); border: 1px solid #1e293b; padding: 16px; border-radius: 8px; margin: 20px 0;">
        <p style="margin: 4px 0; color: #94a3b8;">📅 Timestamp: <strong style="color: #e2e8f0;">{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</strong></p>
        <p style="margin: 4px 0; color: #94a3b8;">🌐 Target Portal: <strong style="color: #e2e8f0;">VFS Bulgaria (New Delhi)</strong></p>
        <p style="margin: 4px 0; color: #94a3b8;">🎯 Monitoring Category: <strong style="color: #f97316;">Long Stay D visa</strong></p>
      </div>
      <p style="color: #64748b; font-size: 13px;">You will receive instant alerts the moment an appointment date becomes available on VFS Global.</p>
    </div>
    """
    return send_email_alert(to_email, subj, html)


# =============================================================================
# BROADCAST DISPATCHERS (Used by 24/7 Slot Watcher)
# =============================================================================

def broadcast_slot_alert(
    category: str,
    date_found: str,
    centre: str,
    screenshot_path: Optional[str] = None
) -> Dict[str, Any]:
    """Broadcast immediate alert to all configured Telegram and Email recipients."""
    from vfs_db import get_slot_monitor_settings, get_all_users

    settings = get_slot_monitor_settings()
    users = get_all_users()

    results = {
        "category": category,
        "date_found": date_found,
        "centre": centre,
        "telegram_sent": 0,
        "email_sent": 0,
        "errors": []
    }

    # 1. Prepare Message Contents
    portal_link = cfg.PORTAL_URL or "https://visa.vfsglobal.com/ind/en/bgr"
    booking_link = f"{portal_link}/book-an-appointment"
    timestamp_str = datetime.now().strftime("%d-%b-%Y %I:%M:%S %p")

    # Telegram Formatted Alert
    tg_text = (
        f"🚨 <b>VFS APPOINTMENT SLOT DETECTED!</b> 🚨\n\n"
        f"🎯 <b>Category:</b> <code>{category}</code>\n"
        f"📅 <b>Available Date:</b> <b><u>{date_found}</u></b>\n"
        f"📍 <b>Centre:</b> {centre}\n"
        f"⏰ <b>Detected At:</b> {timestamp_str}\n\n"
        f"👉 <a href='{booking_link}'><b>Click Here to Open VFS Booking Portal</b></a>\n\n"
        f"⚡ <i>Act immediately to reserve your appointment slot!</i>"
    )

    # HTML Email Alert
    email_subject = f"🚨 VFS SLOT AVAILABLE: {category} — {date_found} ({centre})"
    email_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #0b132b; color: #f8fafc; border: 2px solid #f97316; border-radius: 12px; padding: 24px;">
      <div style="text-align: center; margin-bottom: 20px;">
        <span style="background: #f97316; color: #000; font-weight: 800; font-size: 13px; padding: 4px 12px; border-radius: 20px; text-transform: uppercase;">🔥 Immediate Action Required</span>
        <h1 style="color: #ffffff; margin: 12px 0 6px 0; font-size: 24px;">Appointment Date Available!</h1>
        <p style="color: #94a3b8; margin: 0; font-size: 14px;">VFS Global Bulgaria Appointment Monitor</p>
      </div>

      <div style="background: rgba(30, 41, 59, 0.8); border: 1px solid #334155; border-radius: 10px; padding: 20px; margin-bottom: 24px;">
        <table style="width: 100%; border-collapse: collapse;">
          <tr>
            <td style="padding: 8px 0; color: #94a3b8; font-size: 14px;">Target Category:</td>
            <td style="padding: 8px 0; font-weight: bold; color: #38bdf8; font-size: 15px;">{category}</td>
          </tr>
          <tr>
            <td style="padding: 8px 0; color: #94a3b8; font-size: 14px;">Available Date:</td>
            <td style="padding: 8px 0; font-weight: 800; color: #4ade80; font-size: 18px;">{date_found}</td>
          </tr>
          <tr>
            <td style="padding: 8px 0; color: #94a3b8; font-size: 14px;">Application Centre:</td>
            <td style="padding: 8px 0; color: #f1f5f9; font-size: 14px;">{centre}</td>
          </tr>
          <tr>
            <td style="padding: 8px 0; color: #94a3b8; font-size: 14px;">Detection Time:</td>
            <td style="padding: 8px 0; color: #cbd5e1; font-size: 13px;">{timestamp_str}</td>
          </tr>
        </table>
      </div>

      <div style="text-align: center; margin: 24px 0;">
        <a href="{booking_link}" style="background: linear-gradient(135deg, #f97316 0%, #ea580c 100%); color: #ffffff; text-decoration: none; padding: 14px 28px; font-weight: 800; border-radius: 8px; font-size: 16px; display: inline-block; box-shadow: 0 4px 14px rgba(249, 115, 22, 0.4);">
          Book Appointment Now &rarr;
        </a>
      </div>

      <p style="font-size: 12px; color: #64748b; text-align: center; margin-top: 24px; border-top: 1px solid #1e293b; padding-top: 16px;">
        VFS Global Automation Suite 24/7 Slot Watcher &bull; Generated automatically.
      </p>
    </div>
    """

    # 2. Dispatch Telegram Alerts
    bot_token = settings.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if bot_token and settings.get("telegram_enabled", 1):
        target_chat_ids = set()
        
        # Primary Chat ID
        if settings.get("telegram_chat_id"):
            target_chat_ids.add(str(settings["telegram_chat_id"]).strip())
        if os.getenv("TELEGRAM_CHAT_ID"):
            target_chat_ids.add(str(os.getenv("TELEGRAM_CHAT_ID")).strip())

        # Operator chat IDs
        for u in users:
            if u.get("telegram_chat_id") and u.get("status") == "active":
                target_chat_ids.add(str(u["telegram_chat_id"]).strip())

        for cid in target_chat_ids:
            if not cid:
                continue
            if screenshot_path and os.path.exists(screenshot_path):
                ok, msg = send_telegram_photo(bot_token, cid, screenshot_path, caption=tg_text)
            else:
                ok, msg = send_telegram_message(bot_token, cid, tg_text)
            
            if ok:
                results["telegram_sent"] += 1
            else:
                results["errors"].append(f"Telegram ({cid}): {msg}")

    # 3. Dispatch Email Alerts
    if settings.get("email_enabled", 1):
        email_recipients = set()
        if settings.get("notify_email"):
            email_recipients.add(settings["notify_email"].strip())
        if cfg.APPLICANT_EMAIL:
            email_recipients.add(cfg.APPLICANT_EMAIL.strip())
        
        # Add operator emails
        for u in users:
            if u.get("email") and u.get("status") == "active":
                email_recipients.add(u["email"].strip())

        for rec in email_recipients:
            if not rec or "@" not in rec:
                continue
            ok, msg = send_email_alert(rec, email_subject, email_html, screenshot_path)
            if ok:
                results["email_sent"] += 1
            else:
                results["errors"].append(f"Email ({rec}): {msg}")

    log.info(f"Broadcast alert completed: {results['telegram_sent']} Telegram, {results['email_sent']} Email.")
    return results


def broadcast_daily_report(
    centre: str,
    categories_status: Dict[str, str],
    total_checks_today: int,
    last_found_date: Optional[str] = None
) -> Dict[str, Any]:
    """Send daily 10 PM availability summary report via Telegram and Email."""
    from vfs_db import get_slot_monitor_settings, get_all_users

    settings = get_slot_monitor_settings()
    users = get_all_users()
    today_str = datetime.now().strftime("%d-%b-%Y")
    now_str = datetime.now().strftime("%d-%b-%Y %I:%M %p")

    results = {"telegram_sent": 0, "email_sent": 0, "errors": []}

    # Format category rows for Telegram
    cat_lines = []
    for cat, status in categories_status.items():
        if "no date" in status.lower():
            emoji = "❌"
            val = f"<code>{status}</code>"
        else:
            emoji = "🔥"
            val = f"<b>{status}</b>"
        cat_lines.append(f"{emoji} <b>{cat}:</b> {val}")

    cat_block_tg = "\n".join(cat_lines) if cat_lines else "No data available."

    tg_report = (
        f"📊 <b>VFS DAILY AVAILABILITY REPORT (10 PM)</b>\n"
        f"📅 Date: <b>{today_str}</b> | Time: <code>{now_str}</code>\n"
        f"📍 Centre: <b>{centre}</b>\n\n"
        f"<b>Category Availability Status:</b>\n"
        f"{cat_block_tg}\n\n"
        f"📈 <b>Daily Monitoring Summary:</b>\n"
        f"• Total checks executed today: <b>{total_checks_today}</b>\n"
        f"• Check frequency: <b>Every {settings.get('check_interval_seconds', 30)} seconds</b>\n"
        f"• System status: <b>24/7 Watcher Active</b>\n\n"
        f"👉 <a href='{cfg.PORTAL_URL}'><b>Visit VFS Bulgaria Portal</b></a>"
    )

    # Format category rows for Email
    table_rows = []
    for cat, status in categories_status.items():
        is_avail = "no date" not in status.lower()
        color = "#4ade80" if is_avail else "#94a3b8"
        status_badge = (
            f"<span style='background:rgba(34,197,94,0.2); color:#4ade80; padding:3px 10px; border-radius:12px; font-weight:bold;'>AVAILABLE: {status}</span>"
            if is_avail else
            f"<span style='background:rgba(148,163,184,0.15); color:#94a3b8; padding:3px 10px; border-radius:12px;'>No date available</span>"
        )
        table_rows.append(f"""
          <tr style="border-bottom: 1px solid #1e293b;">
            <td style="padding: 12px 10px; font-weight: 600; color: #f1f5f9;">{cat}</td>
            <td style="padding: 12px 10px; text-align: right;">{status_badge}</td>
          </tr>
        """)
    table_html = "".join(table_rows)

    email_subject = f"📊 Daily VFS Slot Availability Report — {today_str} ({centre})"
    email_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 650px; margin: 0 auto; background: #0f172a; color: #f8fafc; border: 1px solid #334155; border-radius: 12px; padding: 28px;">
      <div style="border-bottom: 1px solid #334155; padding-bottom: 16px; margin-bottom: 20px;">
        <span style="background: #3b82f6; color: #fff; font-size: 12px; font-weight: 800; padding: 3px 10px; border-radius: 20px; text-transform: uppercase;">Daily 10:00 PM Summary</span>
        <h2 style="color: #ffffff; margin: 10px 0 4px 0;">VFS Appointment Availability Report</h2>
        <p style="color: #94a3b8; margin: 0; font-size: 14px;">Centre: <strong style="color: #cbd5e1;">{centre}</strong> &bull; Generated: {now_str}</p>
      </div>

      <h3 style="font-size: 16px; color: #e2e8f0; margin-bottom: 12px;">Category Status Breakdown</h3>
      <table style="width: 100%; border-collapse: collapse; margin-bottom: 24px; background: #1e293b; border-radius: 8px; overflow: hidden;">
        <thead>
          <tr style="background: #0b132b; color: #94a3b8; font-size: 13px; text-align: left;">
            <th style="padding: 10px;">Visa Category</th>
            <th style="padding: 10px; text-align: right;">Earliest Date Status</th>
          </tr>
        </thead>
        <tbody>
          {table_html}
        </tbody>
      </table>

      <div style="background: #1e293b; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
        <h4 style="margin: 0 0 10px 0; color: #38bdf8; font-size: 14px;">Monitoring Metrics</h4>
        <p style="margin: 4px 0; font-size: 13px; color: #cbd5e1;">&bull; Total checks executed today: <strong>{total_checks_today}</strong></p>
        <p style="margin: 4px 0; font-size: 13px; color: #cbd5e1;">&bull; Monitoring interval: <strong>Every {settings.get('check_interval_seconds', 30)} seconds (24/7)</strong></p>
        <p style="margin: 4px 0; font-size: 13px; color: #cbd5e1;">&bull; Target Watcher: <strong>Long Stay D visa</strong></p>
      </div>

      <div style="text-align: center;">
        <a href="{cfg.PORTAL_URL}" style="background: #2563eb; color: #fff; text-decoration: none; padding: 10px 20px; font-weight: 700; border-radius: 6px; font-size: 14px; display: inline-block;">
          Open VFS Bulgaria Portal
        </a>
      </div>
    </div>
    """

    # Dispatch to Telegram
    bot_token = settings.get("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN", "")
    if bot_token and settings.get("telegram_enabled", 1):
        target_chat_ids = set()
        if settings.get("telegram_chat_id"):
            target_chat_ids.add(str(settings["telegram_chat_id"]).strip())
        if os.getenv("TELEGRAM_CHAT_ID"):
            target_chat_ids.add(str(os.getenv("TELEGRAM_CHAT_ID")).strip())
        for u in users:
            if u.get("telegram_chat_id") and u.get("status") == "active":
                target_chat_ids.add(str(u["telegram_chat_id"]).strip())

        for cid in target_chat_ids:
            if not cid:
                continue
            ok, msg = send_telegram_message(bot_token, cid, tg_report)
            if ok:
                results["telegram_sent"] += 1
            else:
                results["errors"].append(f"Telegram ({cid}): {msg}")

    # Dispatch to Email
    if settings.get("email_enabled", 1):
        email_recipients = set()
        if settings.get("notify_email"):
            email_recipients.add(settings["notify_email"].strip())
        if cfg.APPLICANT_EMAIL:
            email_recipients.add(cfg.APPLICANT_EMAIL.strip())
        for u in users:
            if u.get("email") and u.get("status") == "active":
                email_recipients.add(u["email"].strip())

        for rec in email_recipients:
            if not rec or "@" not in rec:
                continue
            ok, msg = send_email_alert(rec, email_subject, email_html)
            if ok:
                results["email_sent"] += 1
            else:
                results["errors"].append(f"Email ({rec}): {msg}")

    log.info(f"Daily report sent: {results['telegram_sent']} Telegram, {results['email_sent']} Email.")
    return results
