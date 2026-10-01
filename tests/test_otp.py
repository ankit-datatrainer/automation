import pytest
import re
from vfs_otp import extract_otp_from_text, get_inbox_baseline
from config import cfg

def test_extract_otp_patterns():
    sample1 = "Dear Customer, Your One Time Password (OTP) for VFS Global appointment booking is 849201. Valid for 10 minutes."
    assert extract_otp_from_text(sample1) == "849201"

    sample2 = "Your verification code: 123456. Do not share this with anyone."
    assert extract_otp_from_text(sample2) == "123456"

    sample3 = "<html><body><p>Hello,</p><p>Use code <strong>654321</strong> to sign in to VFS Global.</p></body></html>"
    assert extract_otp_from_text(sample3) == "654321"

    sample_none = "Your appointment is confirmed for date 2026-10-15."
    assert extract_otp_from_text(sample_none) is None

def test_gmail_imap_baseline():
    baseline_uid, baseline_time = get_inbox_baseline(cfg.VFS_GMAIL_USER, cfg.VFS_GMAIL_APP_PASSWORD)
    assert baseline_uid is not None
    assert baseline_time is not None
