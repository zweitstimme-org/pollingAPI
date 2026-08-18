"""Upcoming election dates from wahlrecht.de, matching website-pipeline.

Landtag dates are scraped from the wahlrecht.de Landtage table. The next
Bundestag date is configured (default 2029-02-25, estimated) until a firm
calendar date is published.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from pollingapi.logging_config import get_logger
from pollingapi.models import Election

logger = get_logger(__name__)

WAHLRECHT_ELECTION_DATES_URL = "https://www.wahlrecht.de/umfragen/landtage/"
DEFAULT_FEDERAL_ELECTION_DATE = "2029-02-25"

STATE_NAME_TO_CODE = {
    "Baden-Württemberg": "BW",
    "Bayern": "BY",
    "Berlin": "BE",
    "Brandenburg": "BB",
    "Bremen": "HB",
    "Hamburg": "HH",
    "Hessen": "HE",
    "Mecklenburg-Vorpommern": "MV",
    "Niedersachsen": "NI",
    "Nordrhein-Westfalen": "NW",
    "Rheinland-Pfalz": "RP",
    "Saarland": "SL",
    "Sachsen": "SN",
    "Sachsen-Anhalt": "ST",
    "Schleswig-Holstein": "SH",
    "Thüringen": "TH",
}

MONTH_MAP = {
    "januar": 1,
    "jan": 1,
    "februar": 2,
    "feb": 2,
    "märz": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "mai": 5,
    "may": 5,
    "juni": 6,
    "jun": 6,
    "juli": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "oktober": 10,
    "okt": 10,
    "november": 11,
    "nov": 11,
    "dezember": 12,
    "dez": 12,
    "dec": 12,
}


@dataclass(frozen=True)
class ScrapedElectionDate:
    """One upcoming election date for an API election key."""

    election_key: str
    election_date: date
    date_is_estimated: bool
    display_date: str | None = None
    state_name: str | None = None


def estimate_date_from_season(season_text: str, year: int) -> datetime:
    season_text = season_text.lower().strip()
    if "frühjahr" in season_text:
        return datetime(year, 4, 15)
    if "herbst" in season_text:
        return datetime(year, 10, 15)
    if "winter" in season_text:
        return datetime(year, 1, 15)
    if "sommer" in season_text:
        return datetime(year, 7, 15)
    return datetime(year, 6, 15)


def find_next_sunday(value: datetime) -> datetime:
    """Snap to the same day if already Sunday, otherwise the following Sunday."""
    days_ahead = (6 - value.weekday()) % 7
    return value + timedelta(days=days_ahead)


def format_display_date(date_text: str) -> str:
    if not date_text:
        return ""
    formatted = re.sub(r"(\D)(\d{4})", r"\1 \2", date_text.strip())
    return re.sub(r"\s+", " ", formatted)


def parse_date_text(date_text: str) -> tuple[datetime | None, bool]:
    if not date_text:
        return None, False

    text = date_text.strip()

    # Exact calendar dates from wahlrecht.de are authoritative — keep them.
    specific_match = re.search(r"(\d+)\.\s*(\w+)\s*(\d{4})", text)
    if specific_match:
        day = int(specific_match.group(1))
        month_name = specific_match.group(2).lower()
        year = int(specific_match.group(3))
        month = MONTH_MAP.get(month_name)
        if month:
            return datetime(year, month, day), False

    season_match = re.search(r"(\w+)\s*(\d{4})", text)
    if season_match:
        season = season_match.group(1)
        year = int(season_match.group(2))
        return find_next_sunday(estimate_date_from_season(season, year)), True

    return None, False


def federal_election_from_env() -> ScrapedElectionDate:
    raw = os.getenv("FEDERAL_ELECTION_DATE", DEFAULT_FEDERAL_ELECTION_DATE).strip()
    estimated_raw = os.getenv("FEDERAL_ELECTION_DATE_IS_ESTIMATED", "true").strip().lower()
    estimated = estimated_raw not in {"0", "false", "no"}
    return ScrapedElectionDate(
        election_key="BUND",
        election_date=date.fromisoformat(raw),
        date_is_estimated=estimated,
        display_date=raw,
        state_name="Deutschland",
    )


def parse_wahlrecht_landtag_html(html: str | bytes) -> list[ScrapedElectionDate]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="wilko")
    if not table:
        raise RuntimeError("Could not find election date table on wahlrecht.de")

    tbody = table.find("tbody") or table
    rows = tbody.find_all("tr")

    elections: list[ScrapedElectionDate] = []
    for row in rows:
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        state_link = cells[0].find("a")
        if not state_link:
            continue

        state_name = " ".join(state_link.get_text(strip=True).split())
        date_text = " ".join(cells[1].get_text(strip=True).split())
        parsed, is_estimated = parse_date_text(date_text)
        if not parsed:
            continue

        state_code = STATE_NAME_TO_CODE.get(state_name)
        if not state_code:
            continue

        elections.append(
            ScrapedElectionDate(
                election_key=state_code,
                election_date=parsed.date(),
                date_is_estimated=is_estimated,
                display_date=format_display_date(date_text),
                state_name=state_name,
            )
        )

    elections.sort(key=lambda item: (item.election_date, item.election_key))
    return elections


def scrape_landtag_election_dates(
    url: str = WAHLRECHT_ELECTION_DATES_URL,
    timeout: int = 30,
) -> list[ScrapedElectionDate]:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return parse_wahlrecht_landtag_html(response.content)


def upcoming_election_dates(
    url: str = WAHLRECHT_ELECTION_DATES_URL,
    timeout: int = 30,
) -> list[ScrapedElectionDate]:
    """Landtag dates from wahlrecht.de plus the configured federal date."""
    dates = scrape_landtag_election_dates(url=url, timeout=timeout)
    return [federal_election_from_env(), *dates]


def apply_election_dates(db: Session, dates: list[ScrapedElectionDate]) -> int:
    """Write upcoming dates onto existing election reference rows.

    Rows that are not in the scrape (EU, Ost, West) are left unchanged.
    """
    updated = 0
    by_key = {item.election_key: item for item in dates}
    rows = db.query(Election).all()
    for row in rows:
        item = by_key.get(row.key)
        if item is None:
            continue
        changed = False
        if row.date != item.election_date:
            row.date = item.election_date
            changed = True
        if row.year != item.election_date.year:
            row.year = item.election_date.year
            changed = True
        if row.date_is_estimated != item.date_is_estimated:
            row.date_is_estimated = item.date_is_estimated
            changed = True
        if changed:
            updated += 1
    if updated:
        db.commit()
    return updated


def refresh_election_dates(db: Session, timeout: int = 30) -> int:
    """Scrape upcoming dates and persist them on election reference rows."""
    dates = upcoming_election_dates(timeout=timeout)
    updated = apply_election_dates(db, dates)
    logger.info(
        "Refreshed election dates: scraped=%s updated=%s",
        len(dates),
        updated,
    )
    return updated
