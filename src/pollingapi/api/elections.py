"""Election-focused API routes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from pollingapi.database import get_db
from pollingapi.models import Election, Poll

router = APIRouter(prefix="/elections", tags=["elections"])
DBSession = Annotated[Session, Depends(get_db)]


class ElectionSummaryItem(BaseModel):
    election_key: str
    election_type: str
    scope: str | None
    year: int | None
    date: str | None
    date_is_estimated: bool | None
    poll_count: int
    latest_publish_date: str | None


def _election_summary_query(db: Session) -> Any:
    return (
        db.query(
            Election.key.label("election_key"),
            Election.election_type,
            Election.scope,
            Election.year,
            Election.date,
            Election.date_is_estimated,
            func.count(Poll.id).label("poll_count"),
            func.max(Poll.publish_date).label("latest_publish_date"),
        )
        .outerjoin(Poll, Poll.election_key == Election.key)
        .group_by(
            Election.key,
            Election.election_type,
            Election.scope,
            Election.year,
            Election.date,
            Election.date_is_estimated,
        )
    )


@router.get("", response_model=list[ElectionSummaryItem])
def list_election_summaries(db: DBSession):
    """List elections with poll counts and next election date."""
    rows = _election_summary_query(db).order_by(Election.key.asc()).all()
    return [_summary_from_row(row) for row in rows]


def _summary_from_row(row: Any) -> ElectionSummaryItem:
    election_date = row.date
    latest = row.latest_publish_date
    return ElectionSummaryItem(
        election_key=row.election_key,
        election_type=row.election_type,
        scope=row.scope,
        year=row.year,
        date=election_date.isoformat() if election_date else None,
        date_is_estimated=row.date_is_estimated,
        poll_count=row.poll_count,
        latest_publish_date=latest.isoformat() if latest else None,
    )


@router.get("/{election_key}", response_model=ElectionSummaryItem)
def get_election_summary(election_key: str, db: DBSession):
    """Get one election summary by ID."""
    row = _election_summary_query(db).filter(Election.key == election_key).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Election {election_key} not found")
    return _summary_from_row(row)
