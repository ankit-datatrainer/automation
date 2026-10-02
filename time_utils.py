from datetime import datetime, timezone, timedelta
from typing import Optional

# Indian Standard Time (IST: UTC+05:30)
IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def get_ist_now() -> datetime:
    """Return the current datetime localized in Indian Standard Time (IST, UTC+5:30)."""
    return datetime.now(IST)


def format_ist_dt(dt: Optional[datetime] = None, fmt: str = "%Y-%m-%d %H:%M:%S IST") -> str:
    """Format a datetime in IST with the given format string."""
    if dt is None:
        dt = get_ist_now()
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    return dt.strftime(fmt)


def format_ist_time(dt: Optional[datetime] = None) -> str:
    """Format time only in IST (e.g. '17:57:29 IST')."""
    return format_ist_dt(dt, "%H:%M:%S IST")


def format_ist_display(dt: Optional[datetime] = None) -> str:
    """Format standard human-friendly IST date and time (e.g. '02-Oct-2026 05:57 PM IST')."""
    return format_ist_dt(dt, "%d-%b-%Y %I:%M %p IST")


def format_ist_date(dt: Optional[datetime] = None) -> str:
    """Format date only in IST (e.g. '2026-10-02')."""
    return format_ist_dt(dt, "%Y-%m-%d")


def format_ist_hm(dt: Optional[datetime] = None) -> str:
    """Format 24h hour and minute in IST for schedule matching (e.g. '22:00')."""
    return format_ist_dt(dt, "%H:%M")
