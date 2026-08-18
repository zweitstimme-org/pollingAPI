"""Tests for upcoming election dates on election reference rows."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from pollingapi.database import Base, get_db
from pollingapi.main import app
from pollingapi.models import Election
from pollingapi.scraper.election_dates import (
    ScrapedElectionDate,
    apply_election_dates,
    federal_election_from_env,
    parse_date_text,
    parse_wahlrecht_landtag_html,
)

WAHLRECHT_HTML = """
<html><body>
<table class="wilko">
<tbody>
<tr>
  <th><a href="/umfragen/sachsen-anhalt.htm">Sachsen-Anhalt</a></th>
  <td>6. September 2026</td>
</tr>
<tr>
  <th><a href="/umfragen/berlin.htm">Berlin</a></th>
  <td>20. September 2026</td>
</tr>
<tr>
  <th><a href="/umfragen/mecklenburg.htm">Mecklenburg-Vorpommern</a></th>
  <td>20. September 2026</td>
</tr>
<tr>
  <th><a href="/umfragen/bayern.htm">Bayern</a></th>
  <td>Herbst 2028</td>
</tr>
</tbody>
</table>
</body></html>
"""


def test_parse_specific_german_date() -> None:
    parsed, estimated = parse_date_text("6. September 2026")
    assert parsed is not None
    assert parsed.date() == date(2026, 9, 6)
    assert estimated is False


def test_parse_season_is_estimated_sunday() -> None:
    parsed, estimated = parse_date_text("Herbst 2028")
    assert parsed is not None
    assert estimated is True
    assert parsed.month == 10
    assert parsed.year == 2028
    assert parsed.weekday() == 6


def test_parse_wahlrecht_table() -> None:
    rows = parse_wahlrecht_landtag_html(WAHLRECHT_HTML)
    by_key = {row.election_key: row for row in rows}
    assert by_key["ST"].election_date == date(2026, 9, 6)
    assert by_key["ST"].date_is_estimated is False
    assert by_key["BE"].election_date == date(2026, 9, 20)
    assert by_key["MV"].election_date == date(2026, 9, 20)
    assert by_key["BY"].date_is_estimated is True


def test_federal_election_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FEDERAL_ELECTION_DATE", "2029-02-25")
    monkeypatch.setenv("FEDERAL_ELECTION_DATE_IS_ESTIMATED", "true")
    federal = federal_election_from_env()
    assert federal.election_key == "BUND"
    assert federal.election_date == date(2029, 2, 25)
    assert federal.date_is_estimated is True


def test_apply_election_dates_updates_matching_rows() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add_all(
            [
                Election(key="ST", election_type="Landtagswahl", scope="st"),
                Election(key="BE", election_type="Landtagswahl", scope="be"),
                Election(key="BUND", election_type="Bundestagswahl", scope="federal"),
                Election(key="OST", election_type="Bundestagswahl", scope="ost"),
            ]
        )
        db.commit()
        updated = apply_election_dates(
            db,
            [
                ScrapedElectionDate(
                    election_key="ST",
                    election_date=date(2026, 9, 6),
                    date_is_estimated=False,
                ),
                ScrapedElectionDate(
                    election_key="BUND",
                    election_date=date(2029, 2, 25),
                    date_is_estimated=True,
                ),
            ],
        )
        assert updated == 2
        st = db.get(Election, "ST")
        bund = db.get(Election, "BUND")
        ost = db.get(Election, "OST")
        assert st is not None
        assert bund is not None
        assert ost is not None
        assert st.date == date(2026, 9, 6)
        assert st.year == 2026
        assert st.date_is_estimated is False
        assert bund.date == date(2029, 2, 25)
        assert bund.year == 2029
        assert bund.date_is_estimated is True
        assert ost.date is None
        assert ost.year is None


def test_v2_elections_expose_next_date() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add(
            Election(
                key="ST",
                election_type="Landtagswahl",
                scope="st",
                year=2026,
                date=date(2026, 9, 6),
                date_is_estimated=False,
            )
        )
        db.commit()

    def override_db() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    with (
        patch("pollingapi.main.init_db_async", return_value=None),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/v2/elections")
        detail = client.get("/v2/elections/ST")
        reference = client.get("/v2/reference-data")
    app.dependency_overrides.clear()

    assert response.status_code == 200
    item = next(row for row in response.json() if row["election_key"] == "ST")
    assert item["date"] == "2026-09-06"
    assert item["year"] == 2026
    assert item["date_is_estimated"] is False

    assert detail.status_code == 200
    assert detail.json()["date"] == "2026-09-06"

    assert reference.status_code == 200
    elections = reference.json()["elections"]
    st = next(row for row in elections if row["key"] == "ST")
    assert st["date"] == "2026-09-06"
    assert st["date_is_estimated"] is False
