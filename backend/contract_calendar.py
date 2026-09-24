"""NYMEX WTI (CL) contract calendar: business days, last trade dates, and the front contract.

One source of truth for everything that depends on which WTI contract is "front": the dashboard's
contract label, the live track record's contract-roll skip, and the research scripts' roll-free
return construction. Pure standard library so every entry point (including the CI recorder) can
import it cheaply.

Rules implemented (CME Rulebook, Light Sweet Crude Oil futures, chapter 200):
  - Trading in a contract terminates 3 business days before the 25th calendar day of the month
    PRIOR to the contract month. If the 25th is not a business day, trading terminates 4 business
    days before the 25th.
  - Business days exclude weekends and the CME Group holidays on which energy futures do not
    settle: New Year's Day, Martin Luther King Jr. Day, Presidents' Day, Good Friday, Memorial Day,
    Juneteenth (from 2022), Independence Day, Labor Day, Thanksgiving and Christmas. Columbus Day
    and Veterans Day are normal trading days.
  - The CME trading day for a moment in time starts at 18:00 ET the previous evening (Globex
    reopen), so a quote at 19:00 ET on Monday belongs to Tuesday's session.
  - Yahoo's continuous CL=F (like EIA's "contract 1") tracks the expiring contract through its
    last trade date and rolls to the next contract on the following trading day. The front contract
    for a trading date is therefore the first contract whose last trade date is on or after it.

Unscheduled closures (e.g. 2001-09-11, 2012-10-29) are not modelled; callers that have an observed
exchange calendar (a price history) can pass their own `is_business` predicate instead.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Callable
from zoneinfo import ZoneInfo

MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}
EXCHANGE_TZ = ZoneInfo("America/New_York")
SESSION_OPEN_ET = time(18, 0)          # Globex reopen: the next trading day's session begins

BusinessDayFn = Callable[[date], bool]


def _easter_sunday(year: int) -> date:
    """Gregorian Easter (Anonymous Gregorian / Meeus-Jones-Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741 - canonical algorithm variable name
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th (1-based) given weekday of a month; n = -1 means the last one."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date, saturday_to_friday: bool = True) -> date | None:
    """Weekend observance: Sunday -> Monday; Saturday -> Friday (or no holiday)."""
    if day.weekday() == 6:
        return day + timedelta(days=1)
    if day.weekday() == 5:
        return day - timedelta(days=1) if saturday_to_friday else None
    return day


@lru_cache(maxsize=256)
def nymex_holidays(year: int) -> frozenset[date]:
    """CME Group holidays on which NYMEX energy futures do not settle, for one calendar year."""
    days = [
        _observed(date(year, 1, 1), saturday_to_friday=False),   # Saturday New Year: no holiday
        _nth_weekday(year, 1, 0, 3) if year >= 1998 else None,  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),                             # Presidents' Day
        _easter_sunday(year) - timedelta(days=2),                # Good Friday
        _nth_weekday(year, 5, 0, -1),                            # Memorial Day
        _observed(date(year, 6, 19)) if year >= 2022 else None,  # Juneteenth
        _observed(date(year, 7, 4)),                             # Independence Day
        _nth_weekday(year, 9, 0, 1),                             # Labor Day
        _nth_weekday(year, 11, 3, 4),                            # Thanksgiving
        _observed(date(year, 12, 25)),                           # Christmas
    ]
    return frozenset(d for d in days if d is not None and d.year == year)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and day not in nymex_holidays(day.year)


def shift_business_days(day: date, n: int, is_business: BusinessDayFn = is_business_day) -> date:
    """The n-th business day after (n > 0) or before (n < 0) `day`, not counting `day` itself."""
    step = 1 if n > 0 else -1
    remaining = abs(n)
    cur = day
    while remaining:
        cur += timedelta(days=step)
        if is_business(cur):
            remaining -= 1
    return cur


def business_days_between(start: date, end: date, is_business: BusinessDayFn = is_business_day) -> int:
    """Business days in (start, end]; negative when end precedes start."""
    if end < start:
        return -business_days_between(end, start, is_business)
    count, cur = 0, start
    while cur < end:
        cur += timedelta(days=1)
        count += is_business(cur)
    return count


def last_trade_date(year: int, month: int, is_business: BusinessDayFn = is_business_day) -> date:
    """Last trading day of the CL contract for delivery month (year, month)."""
    prior_year, prior_month = (year - 1, 12) if month == 1 else (year, month - 1)
    twenty_fifth = date(prior_year, prior_month, 25)
    back = 3 if is_business(twenty_fifth) else 4
    return shift_business_days(twenty_fifth, -back, is_business)


def next_contract(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def contract_code(year: int, month: int) -> str:
    """Exchange-style code, e.g. (2026, 11) -> 'CLX26'."""
    return f"CL{MONTH_CODES[month]}{year % 100:02d}"


def yahoo_symbol(year: int, month: int) -> str:
    """Yahoo Finance ticker for a specific contract, e.g. 'CLX26.NYM' (the bare code never resolves)."""
    return f"{contract_code(year, month)}.NYM"


def trading_date(moment: datetime | date) -> date:
    """CME trading date of a moment: the session opening at 18:00 ET belongs to the next business day.

    A plain `date` is taken as a session date and rolled forward only if it is not a business day.
    """
    if isinstance(moment, datetime):
        aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
        local = aware.astimezone(EXCHANGE_TZ)
        day = local.date()
        if local.time() >= SESSION_OPEN_ET:
            day += timedelta(days=1)
    else:
        day = moment
    while not is_business_day(day):
        day += timedelta(days=1)
    return day


def market_is_open(moment: datetime) -> bool:
    """Whether CL trades on Globex at `moment`.

    A business-day session runs from 18:00 ET on the previous calendar evening to 17:00 ET, which
    yields the Sunday-evening open, the Friday 17:00 close, the daily 17:00-18:00 break, and closed
    holidays. Abbreviated holiday sessions (no settlement) are treated as closed — conservative for
    anything that must price a real session.
    """
    aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    local = aware.astimezone(EXCHANGE_TZ)
    session = trading_date(aware)
    opens = datetime.combine(session - timedelta(days=1), SESSION_OPEN_ET, tzinfo=EXCHANGE_TZ)
    closes = datetime.combine(session, time(17, 0), tzinfo=EXCHANGE_TZ)
    return opens <= local < closes


def front_contract(moment: datetime | date, is_business: BusinessDayFn = is_business_day) -> tuple[int, int]:
    """(year, month) of the contract CL=F tracks on the trading date of `moment`."""
    day = trading_date(moment) if isinstance(moment, datetime) else moment
    year, month = next_contract(day.year, day.month)
    while last_trade_date(year, month, is_business) < day:
        year, month = next_contract(year, month)
    return year, month


def front_contract_code(moment: datetime | date) -> str:
    return contract_code(*front_contract(moment))


def spans_roll(start: datetime | date, end: datetime | date) -> bool:
    """True when CL=F points at a different contract at `end` than at `start`."""
    return front_contract(start) != front_contract(end)
