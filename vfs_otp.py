"""Automated Gmail OTP retrieval module via secure IMAP."""

import email
from email.header import decode_header
import imaplib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

log = logging.getLogger("vfs.otp")


def clean_header_text(header_val: str) -> str:
    """Decode encoded email header into clean unicode string."""
    if not header_val:
        return ""
    decoded_parts = decode_header(header_val)
    out = []
    for part, enc in decoded_parts:
        if isinstance(part, bytes):
            try:
                out.append(part.decode(enc or "utf-8", errors="replace"))
            except Exception:
                out.append(part.decode("latin1", errors="replace"))
        else:
            out.append(str(part))
    return "".join(out)


def extract_otp_from_text(text: str) -> Optional[str]:
    """Extract a 6-digit OTP code from email text or HTML content."""
    if not text:
        return None

    # Strip HTML tags
    clean_text = re.sub(r"<[^>]+>", " ", text)

    # Contextual matches first (highest confidence)
    context_patterns = [
        r"(?:otp|code|one\s*time\s*password|verification\s*code|pin)[\s:is\-\*#]*([0-9]{6})\b",
        r"\b([0-9]{6})\b[\s]*(?:is\s*your\s*one\s*time\s*password|is\s*your\s*otp|is\s*your\s*verification\s*code)",
        r"(?:use\s*code)[\s:is\-\*#]*([0-9]{6})\b",
    ]
    for pattern in context_patterns:
        match = re.search(pattern, clean_text, re.IGNORECASE)
        if match:
            return match.group(1)

    # General 6-digit token match (avoiding common false positives)
    all_6_digits = re.findall(r"\b([0-9]{6})\b", clean_text)
    for code in all_6_digits:
        # Ignore obvious years or sequential numbers
        if code.startswith(("19", "20")):
            continue
        return code

    # If any 6-digit remains, fallback
    if all_6_digits:
        return all_6_digits[0]

    return None


def get_inbox_baseline(
    user: str,
    app_password: str,
    host: str = "imap.gmail.com",
    port: int = 993
) -> Tuple[int, datetime]:
    """Record current highest UID in inbox before triggering an OTP send."""
    clean_pw = app_password.replace(" ", "")
    mail = imaplib.IMAP4_SSL(host, port)
    try:
        mail.login(user, clean_pw)
        mail.select("INBOX", readonly=True)

        res, data = mail.uid("SEARCH", None, "ALL")
        highest_uid = 0
        if res == "OK" and data and data[0]:
            uids = [int(x) for x in data[0].split() if x.isdigit()]
            if uids:
                highest_uid = max(uids)

        return highest_uid, datetime.now(timezone.utc)
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_latest_otp(
    user: str,
    app_password: str,
    baseline_uid: int = 0,
    baseline_time: Optional[datetime] = None,
    timeout_seconds: int = 90,
    poll_interval: int = 3,
    log_callback: Optional[Callable[[str], None]] = None,
    host: str = "imap.gmail.com",
    port: int = 993
) -> Optional[str]:
    """Poll Gmail inbox for a new OTP email arrived after the baseline."""
    clean_pw = app_password.replace(" ", "")
    start_time = time.monotonic()

    def report(msg: str):
        log.info(msg)
        if log_callback:
            log_callback(msg)

    report(f"Connecting to Gmail ({user}) via IMAP to monitor fresh OTP...")

    while time.monotonic() - start_time < timeout_seconds:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(host, port)
            mail.login(user, clean_pw)
            mail.select("INBOX", readonly=True)

            # Search messages with UID > baseline_uid if baseline is positive
            if baseline_uid > 0:
                search_criterion = f"(UID {baseline_uid + 1}:*)"
            else:
                search_criterion = "ALL"

            res, data = mail.uid("SEARCH", None, search_criterion)
            if res == "OK" and data and data[0]:
                uids = [int(x) for x in data[0].split() if x.isdigit()]
                # Filter strictly greater than baseline_uid
                candidate_uids = [u for u in uids if u > baseline_uid]
                candidate_uids.sort(reverse=True)  # Newest first

                for uid in candidate_uids:
                    f_res, msg_data = mail.uid("FETCH", str(uid), "(RFC822)")
                    if f_res != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    subject = clean_header_text(msg.get("Subject", ""))
                    sender = clean_header_text(msg.get("From", ""))
                    date_header = clean_header_text(msg.get("Date", ""))

                    # Extract body content
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            cdispo = str(part.get("Content-Disposition"))
                            if ctype in ("text/plain", "text/html") and "attachment" not in cdispo:
                                payload = part.get_payload(decode=True)
                                if payload:
                                    body += payload.decode("utf-8", errors="replace") + " "
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="replace")

                    # Check for VFS / OTP relevance in Subject or Sender or Body
                    combined_text = f"{subject} {body}"
                    is_relevant = (
                        "vfs" in sender.lower()
                        or "vfs" in subject.lower()
                        or "otp" in subject.lower()
                        or "verification" in subject.lower()
                        or "one time password" in combined_text.lower()
                    )

                    if is_relevant:
                        otp_code = extract_otp_from_text(combined_text)
                        if otp_code:
                            report(f"Found OTP {otp_code} in email UID {uid} (Subject: '{subject}')")
                            return otp_code

        except Exception as e:
            report(f"IMAP poll warning: {e}")
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass

        time.sleep(poll_interval)

    report("Timed out waiting for OTP from Gmail.")
    return None
