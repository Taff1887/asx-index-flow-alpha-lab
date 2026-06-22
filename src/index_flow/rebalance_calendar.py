"""Scheduled rebalance / reconstitution calendar for major index families.

These cadences are **public schedule reference** (when reviews happen), NOT the
constituent changes themselves — the actual add/delete lists are licensed and
must be supplied via ``data/manual/rebalance_announcements/``. The calendar gives
the *windows* the discovery loop should watch and lets strategies know how many
days remain to an effective date.

Encoded cadences (approximate; verify against the provider each year):
* **S&P/ASX** quarterly — announce ~1st Friday, effective after close 3rd Friday
  of Mar/Jun/Sep/Dec.
* **FTSE Russell** quarterly — effective 3rd Friday of Mar/Jun/Sep/Dec.
* **MSCI** SAIR (May/Nov) & QIR (Feb/Aug) — effective last business day; announce
  ~9 business days prior.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta

import pandas as pd

CALENDAR_COLUMNS = [
    "family", "event_type", "review_label", "announcement_date_est", "effective_date", "notes"
]


def nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth (1-based) weekday (Mon=0..Sun=6) in a month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def third_friday(year: int, month: int) -> date:
    return nth_weekday(year, month, weekday=4, n=3)


def last_business_day(year: int, month: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    while last.weekday() >= 5:  # Sat/Sun
        last -= timedelta(days=1)
    return last


def _business_days_before(d: date, n: int) -> date:
    cur = d
    while n > 0:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur


def spdji_asx_quarterly(year: int) -> list[dict]:
    rows = []
    for month in (3, 6, 9, 12):
        eff = third_friday(year, month)
        ann = nth_weekday(year, month, weekday=4, n=1)  # ~first Friday
        rows.append(
            {
                "family": "S&P/ASX",
                "event_type": "REBALANCE",
                "review_label": f"{year}-Q{month // 3}",
                "announcement_date_est": ann,
                "effective_date": eff,
                "notes": "Effective after close 3rd Friday; constituents licensed (manual).",
            }
        )
    return rows


def ftse_quarterly(year: int) -> list[dict]:
    rows = []
    for month in (3, 6, 9, 12):
        eff = third_friday(year, month)
        rows.append(
            {
                "family": "FTSE Russell",
                "event_type": "RECONSTITUTION",
                "review_label": f"{year}-Q{month // 3}",
                "announcement_date_est": _business_days_before(eff, 10),
                "effective_date": eff,
                "notes": "Quarterly review effective 3rd Friday.",
            }
        )
    return rows


def msci_reviews(year: int) -> list[dict]:
    rows = []
    schedule = {2: "QIR-Feb", 5: "SAIR-May", 8: "QIR-Aug", 11: "SAIR-Nov"}
    for month, label in schedule.items():
        eff = last_business_day(year, month)
        rows.append(
            {
                "family": "MSCI",
                "event_type": "RECONSTITUTION",
                "review_label": f"{year}-{label}",
                "announcement_date_est": _business_days_before(eff, 9),
                "effective_date": eff,
                "notes": "MSCI index review; pro-forma changes licensed (manual).",
            }
        )
    return rows


def generate_calendar(start_year: int, end_year: int) -> pd.DataFrame:
    """All scheduled review windows across families for [start_year, end_year]."""
    rows: list[dict] = []
    for y in range(start_year, end_year + 1):
        rows += spdji_asx_quarterly(y)
        rows += ftse_quarterly(y)
        rows += msci_reviews(y)
    df = pd.DataFrame(rows, columns=CALENDAR_COLUMNS)
    return df.sort_values("effective_date").reset_index(drop=True)


def upcoming(as_of: date | None = None, horizon_days: int = 120) -> pd.DataFrame:
    """Scheduled reviews with an effective date within the next ``horizon_days``.

    ``as_of`` must be supplied by the caller (no implicit 'today' so behaviour is
    deterministic and testable)."""
    if as_of is None:
        raise ValueError("upcoming() requires an explicit as_of date")
    cal = generate_calendar(as_of.year, as_of.year + 1)
    end = as_of + timedelta(days=horizon_days)
    mask = (cal["effective_date"] >= as_of) & (cal["effective_date"] <= end)
    return cal[mask].reset_index(drop=True)


def load_manual_rebalance_announcements(cfg) -> pd.DataFrame:
    """Load real announced add/delete rows dropped into
    data/manual/rebalance_announcements/*.csv."""
    folder = cfg.path("data_manual") / "rebalance_announcements"
    if not folder.exists():
        return pd.DataFrame()
    frames = []
    for csv in sorted(folder.glob("*.csv")):
        if csv.name.startswith("_TEMPLATE"):
            continue
        try:
            frames.append(pd.read_csv(csv))
        except Exception:  # noqa: BLE001
            continue
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
