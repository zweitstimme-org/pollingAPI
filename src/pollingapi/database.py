"""Database configuration and connection management."""

from contextlib import suppress

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from pollingapi.core import settings

# Create base class for models
Base = declarative_base()

# Synchronous engine and session
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    echo=False,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async engine and session
async_engine = create_async_engine(
    settings.async_database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.async_database_url else {},
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


def _apply_schema_migrations():
    """Apply incremental schema changes that cannot be expressed via create_all.

    Safe to run on every startup: each migration is guarded by a presence check
    so it is a no-op when the column/index already exists.  Only touches tables
    that actually exist — new databases are fully covered by create_all.
    """
    validation_column_renames = {
        "party_percentage_range": "qc_party_percentage_range",
        "result_sum_check": "qc_result_sum_check",
        "date_consistency": "qc_date_consistency",
        "respondents_plausible": "qc_respondents_plausible",
        "core_parties_present": "qc_core_parties_present",
        "institute_result_jump": "qc_institute_result_jump",
        "scope_result_jump": "qc_scope_result_jump",
    }
    pipeline_run_columns = {
        "validation_status": "TEXT",
        "validation_total_polls": "INTEGER DEFAULT 0",
        "validation_valid_polls": "INTEGER DEFAULT 0",
        "validation_invalid_polls": "INTEGER DEFAULT 0",
        "validation_warning_polls": "INTEGER DEFAULT 0",
        "validation_valid_share": "FLOAT",
    }
    poll_columns = {
        "matching_poll_id": "INTEGER",
        "matching_status": "TEXT",
        "is_public": "BOOLEAN DEFAULT FALSE",
        "public_exclusion_reason": "TEXT",
    }
    election_columns = {
        "date": "DATE",
        "date_is_estimated": "BOOLEAN",
    }

    with engine.connect() as conn:
        # --- polls_raw incremental columns -----------------------------------
        if "sqlite" in settings.database_url:
            # Check whether the table exists at all before inspecting columns
            tables = {
                row[0]
                for row in conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table'")
                ).fetchall()
            }
            if "polls_raw" in tables:
                rows = conn.execute(text("PRAGMA table_info(polls_raw)")).fetchall()
                existing_columns = {row[1] for row in rows}

                if "pipeline_run_id" not in existing_columns:
                    conn.execute(text("ALTER TABLE polls_raw ADD COLUMN pipeline_run_id TEXT"))
                    conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_polls_raw_pipeline_run_id"
                            " ON polls_raw (pipeline_run_id)"
                        )
                    )

                if "worker" not in existing_columns:
                    conn.execute(text("ALTER TABLE polls_raw ADD COLUMN worker TEXT"))

                if "survey_type" not in existing_columns:
                    conn.execute(text("ALTER TABLE polls_raw ADD COLUMN survey_type TEXT"))

                if "duplicate_of_poll_id" not in existing_columns:
                    conn.execute(
                        text("ALTER TABLE polls_raw ADD COLUMN duplicate_of_poll_id INTEGER")
                    )
                    conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_polls_raw_duplicate_of_poll_id"
                            " ON polls_raw (duplicate_of_poll_id)"
                        )
                    )

            if "polls" in tables:
                rows = conn.execute(text("PRAGMA table_info(polls)")).fetchall()
                existing_columns = {row[1] for row in rows}

                if "fingerprint" not in existing_columns:
                    conn.execute(text("ALTER TABLE polls ADD COLUMN fingerprint TEXT"))
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_polls_fingerprint"
                        " ON polls (fingerprint)"
                    )
                )

            if "polls" in tables:
                rows = conn.execute(text("PRAGMA table_info(polls)")).fetchall()
                polls_columns = {row[1] for row in rows}
                for column_name, column_type in poll_columns.items():
                    if column_name not in polls_columns:
                        conn.execute(
                            text(f"ALTER TABLE polls ADD COLUMN {column_name} {column_type}")
                        )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_polls_matching_poll_id"
                        " ON polls (matching_poll_id)"
                    )
                )
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_polls_is_public ON polls (is_public)")
                )

            if "elections" in tables:
                rows = conn.execute(text("PRAGMA table_info(elections)")).fetchall()
                election_existing = {row[1] for row in rows}
                for column_name, column_type in election_columns.items():
                    if column_name not in election_existing:
                        conn.execute(
                            text(f"ALTER TABLE elections ADD COLUMN {column_name} {column_type}")
                        )

            if "parties" in tables:
                rows = conn.execute(text("PRAGMA table_info(parties)")).fetchall()
                party_columns = {row[1] for row in rows}
                if "external_ids" not in party_columns:
                    conn.execute(text("ALTER TABLE parties ADD COLUMN external_ids JSON"))

            if "poll_validations" in tables:
                rows = conn.execute(text("PRAGMA table_info(poll_validations)")).fetchall()
                validation_columns = {row[1] for row in rows}
                for old_name, new_name in validation_column_renames.items():
                    if old_name in validation_columns and new_name not in validation_columns:
                        conn.execute(
                            text(
                                f"ALTER TABLE poll_validations RENAME COLUMN"
                                f" {old_name} TO {new_name}"
                            )
                        )

            if "pipeline_runs" in tables:
                rows = conn.execute(text("PRAGMA table_info(pipeline_runs)")).fetchall()
                pipeline_columns = {row[1] for row in rows}
                for column_name, column_type in pipeline_run_columns.items():
                    if column_name not in pipeline_columns:
                        conn.execute(
                            text(
                                f"ALTER TABLE pipeline_runs ADD COLUMN {column_name} {column_type}"
                            )
                        )

            conn.commit()
        else:
            # PostgreSQL / other dialects
            raw_table_exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = 'polls_raw' LIMIT 1"
                )
            ).fetchone()
            if raw_table_exists:
                result = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'polls_raw' AND column_name = 'pipeline_run_id'"
                    )
                )
                if result.fetchone() is None:
                    conn.execute(
                        text("ALTER TABLE polls_raw ADD COLUMN pipeline_run_id VARCHAR(36)")
                    )
                    conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_polls_raw_pipeline_run_id"
                            " ON polls_raw (pipeline_run_id)"
                        )
                    )

                worker_result = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'polls_raw' AND column_name = 'worker'"
                    )
                )
                if worker_result.fetchone() is None:
                    conn.execute(text("ALTER TABLE polls_raw ADD COLUMN worker VARCHAR(100)"))

                survey_type_result = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'polls_raw' AND column_name = 'survey_type'"
                    )
                )
                if survey_type_result.fetchone() is None:
                    conn.execute(text("ALTER TABLE polls_raw ADD COLUMN survey_type VARCHAR(100)"))

                duplicate_result = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'polls_raw' AND column_name = 'duplicate_of_poll_id'"
                    )
                )
                if duplicate_result.fetchone() is None:
                    conn.execute(
                        text("ALTER TABLE polls_raw ADD COLUMN duplicate_of_poll_id INTEGER")
                    )
                    conn.execute(
                        text(
                            "CREATE INDEX IF NOT EXISTS ix_polls_raw_duplicate_of_poll_id"
                            " ON polls_raw (duplicate_of_poll_id)"
                        )
                    )

            polls_table_exists = conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = 'polls' LIMIT 1")
            ).fetchone()
            if polls_table_exists:
                fingerprint_result = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'polls' AND column_name = 'fingerprint'"
                    )
                )
                if fingerprint_result.fetchone() is None:
                    conn.execute(text("ALTER TABLE polls ADD COLUMN fingerprint VARCHAR(64)"))
                conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS ix_polls_fingerprint"
                        " ON polls (fingerprint)"
                    )
                )

            polls_exists = conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = 'polls' LIMIT 1")
            ).fetchone()
            if polls_exists:
                for column_name, column_type in {
                    "matching_poll_id": "INTEGER",
                    "matching_status": "VARCHAR(50)",
                    "is_public": "BOOLEAN DEFAULT FALSE",
                    "public_exclusion_reason": "VARCHAR(100)",
                }.items():
                    column = conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns"
                            " WHERE table_name = 'polls' AND column_name = :column_name"
                        ),
                        {"column_name": column_name},
                    ).fetchone()
                    if column is None:
                        conn.execute(
                            text(f"ALTER TABLE polls ADD COLUMN {column_name} {column_type}")
                        )
                conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_polls_matching_poll_id"
                        " ON polls (matching_poll_id)"
                    )
                )
                conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_polls_is_public ON polls (is_public)")
                )

            elections_exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = 'elections' LIMIT 1"
                )
            ).fetchone()
            if elections_exists:
                for column_name, column_type in {
                    "date": "DATE",
                    "date_is_estimated": "BOOLEAN",
                }.items():
                    column = conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns"
                            " WHERE table_name = 'elections' AND column_name = :column_name"
                        ),
                        {"column_name": column_name},
                    ).fetchone()
                    if column is None:
                        conn.execute(
                            text(f"ALTER TABLE elections ADD COLUMN {column_name} {column_type}")
                        )

            parties_exists = conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_name = 'parties' LIMIT 1")
            ).fetchone()
            if parties_exists:
                column = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'parties' AND column_name = 'external_ids'"
                    )
                ).fetchone()
                if column is None:
                    conn.execute(text("ALTER TABLE parties ADD COLUMN external_ids JSON"))

            for old_name, new_name in validation_column_renames.items():
                old_column = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'poll_validations' AND column_name = :column_name"
                    ),
                    {"column_name": old_name},
                ).fetchone()
                new_column = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'poll_validations' AND column_name = :column_name"
                    ),
                    {"column_name": new_name},
                ).fetchone()
                if old_column is not None and new_column is None:
                    conn.execute(
                        text(f"ALTER TABLE poll_validations RENAME COLUMN {old_name} TO {new_name}")
                    )

            for column_name, column_type in pipeline_run_columns.items():
                column = conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'pipeline_runs' AND column_name = :column_name"
                    ),
                    {"column_name": column_name},
                ).fetchone()
                if column is None:
                    conn.execute(
                        text(f"ALTER TABLE pipeline_runs ADD COLUMN {column_name} {column_type}")
                    )

            conn.commit()


# Run schema migrations eagerly so any process that imports this module
# (including test clients that mock init_db_async) always works with an
# up-to-date schema on existing databases.
with suppress(Exception):
    _apply_schema_migrations()


def get_db():
    """Get synchronous database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_async_db():
    """Get asynchronous database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def init_db(drop_all: bool = False):
    """Initialize database tables.

    Args:
        drop_all: If True, drop all tables before creating them.
    """
    if drop_all:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _apply_schema_migrations()


async def init_db_async():
    """Initialize database tables asynchronously."""
    # Import models to ensure they're registered

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Schema migrations run synchronously (rare, fast, idempotent)
    _apply_schema_migrations()


# --- Reference data seeding -------------------------------------------------


def seed_all_from_json(db: Session) -> dict:
    """Seed all reference tables from JSON files.

    Uses the JSON files in the json/ directory to seed reference tables
    with the exact primary keys defined in those files.
    """
    from pollingapi.database_seed import seed_all_from_json as _seed_from_json

    return _seed_from_json(db)
