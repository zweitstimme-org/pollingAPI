"""CLI entry points for zweitstimme."""

from pathlib import Path
from typing import Annotated

import typer
from httpx import HTTPError
from sqlalchemy import text
from sqlalchemy.orm import Session

from pollingapi.cleaner import run_cleaning_pipeline
from pollingapi.core import PROJECT_ROOT, settings
from pollingapi.data_validation import DataValidationService, ValidationReportService
from pollingapi.data_validation.config import CONFIG_PATH, PUBLIC_POLICY_PATH, get_validation_config
from pollingapi.data_validation.service import CHECK_NAMES
from pollingapi.database import SessionLocal, init_db, seed_all_from_json
from pollingapi.importer import DEFAULT_MANIFEST, IMPORTS_DIR, ImportRunner, download_from_manifest
from pollingapi.logging_config import get_logger, setup_logging
from pollingapi.notifications import PipelineRunResult, create_notification_manager
from pollingapi.scraper.context import RunContext
from pollingapi.scraper.datamodel import Party as PartyDefinition
from pollingapi.scraper.datamodel import enum_key
from pollingapi.scraper.runner import ScraperRunner
from pollingapi.services import ExportService, ReportService, S3Service

# Initialize logging with default settings
setup_logging()

app = typer.Typer(
    help="Zweitstimme CLI - German Election Polling Data Management",
    no_args_is_help=True,
)
logger = get_logger(__name__)

ARCHIVE_EXPORT_GROUPS = {
    "public": [
        "polls.json",
        "polls.csv",
        "polls.parquet",
        "poll_results.json",
        "poll_results.csv",
        "poll_results.parquet",
        "polls_without_results.json",
        "polls_without_results.csv",
        "polls_without_results.parquet",
    ],
    "cleaned": [
        "all_cleaned_polls.json",
        "all_cleaned_polls.csv",
        "all_cleaned_polls.parquet",
        "all_cleaned_poll_results.json",
        "all_cleaned_poll_results.csv",
        "all_cleaned_poll_results.parquet",
    ],
    "raw": [
        "polls_raw.json",
        "polls_raw.csv",
        "polls_raw.parquet",
    ],
}


def _stage_archive_bundle(target: Path) -> None:
    """Stage the accountability archive layout."""
    import shutil

    export_dir = settings.export_dir
    for group, filenames in ARCHIVE_EXPORT_GROUPS.items():
        group_dir = target / group
        group_dir.mkdir(parents=True, exist_ok=True)
        for filename in filenames:
            source = export_dir / filename
            if source.exists():
                shutil.copy2(source, group_dir / filename)

    metadata = export_dir / "metadata.json"
    if metadata.exists():
        shutil.copy2(metadata, target / "manifest.json")

    reference_dir = target / "reference"
    shutil.copytree(PROJECT_ROOT / "json", reference_dir)

    config_dir = target / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("validation.toml", "pyproject.toml"):
        source = PROJECT_ROOT / filename
        if source.exists():
            shutil.copy2(source, config_dir / filename)

    if settings.report_dir.exists():
        report_dir = target / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        for report in settings.report_dir.glob("*.pdf"):
            shutil.copy2(report, report_dir / report.name)


ImportFileArg = Annotated[
    Path,
    typer.Argument(help="Import file path, relative to ./imports if not absolute"),
]
ImportSourceOption = Annotated[str, typer.Option("--source", "-s", help="Import source name")]
ImportPreviewLimitOption = Annotated[
    int,
    typer.Option("--limit", "-l", help="Number of rows to preview"),
]
ImportManifestOption = Annotated[
    Path,
    typer.Option("--manifest", "-m", help="Download manifest path"),
]


def get_db() -> Session:
    """Get database session."""
    return SessionLocal()


def _validate_public_policy(
    config_path: Path = CONFIG_PATH,
    public_policy_path: Path = PUBLIC_POLICY_PATH,
) -> list[str]:
    """Return policy configuration errors."""
    get_validation_config.cache_clear()
    config = get_validation_config(config_path, public_policy_path)
    errors: list[str] = []
    check_names = set(CHECK_NAMES)
    party_keys = {enum_key(party) for party in PartyDefinition}
    public_dataset = config.public_dataset
    selection = public_dataset.selection
    presence_policy = config.core_parties.presence_policy

    for field_name, values in (
        ("public_dataset.required_checks", public_dataset.required_checks),
        ("public_dataset.exclude_failed_checks", public_dataset.exclude_failed_checks),
    ):
        unknown = sorted(set(values) - check_names)
        if unknown:
            errors.append(f"{field_name} has unknown check(s): {', '.join(unknown)}")

    if not 1800 <= selection.cutoff_year <= 2100:
        errors.append("public_dataset.selection.cutoff_year must be between 1800 and 2100")

    for field_name, value in (
        ("pre_cutoff_provider", selection.pre_cutoff_provider),
        ("post_cutoff_provider", selection.post_cutoff_provider),
        ("secondary_provider", selection.secondary_provider),
    ):
        if not value.strip():
            errors.append(f"public_dataset.selection.{field_name} must not be empty")

    if presence_policy.min_comparison_polls < 1:
        errors.append("core_parties.presence_policy.min_comparison_polls must be at least 1")
    if presence_policy.window_days < 1:
        errors.append("core_parties.presence_policy.window_days must be at least 1")
    if not 0 < presence_policy.min_presence_share <= 1:
        errors.append(
            "core_parties.presence_policy.min_presence_share must be greater than 0 and at most 1"
        )

    for index, rule in enumerate(config.core_parties.rules, start=1):
        prefix = f"core_parties.rules[{index}]"
        if not rule.scope.strip():
            errors.append(f"{prefix}.scope must not be empty")
        if not rule.parties:
            errors.append(f"{prefix}.parties must not be empty")
        unknown_parties = sorted(set(rule.parties) - party_keys)
        if unknown_parties:
            errors.append(
                f"{prefix}.parties has unknown party key(s): {', '.join(unknown_parties)}"
            )
        if (
            rule.from_year is not None
            and rule.to_year is not None
            and rule.from_year > rule.to_year
        ):
            errors.append(f"{prefix}.from_year must be less than or equal to to_year")

    return errors


@app.command(name="policy:validate")
def policy_validate():
    """Validate public_policy.yaml and related validation settings."""
    try:
        errors = _validate_public_policy()
    except Exception as exc:
        typer.echo(f"✗ Public policy invalid: {exc}", err=True)
        raise typer.Exit(1) from exc

    if errors:
        typer.echo("✗ Public policy invalid:", err=True)
        for error in errors:
            typer.echo(f"  - {error}", err=True)
        raise typer.Exit(1)

    typer.echo(f"✓ Public policy valid: {PUBLIC_POLICY_PATH}")


@app.command(name="db:ping")
def db_ping():
    """Verify database connectivity."""
    db = get_db()
    try:
        db.execute(text("SELECT 1"))
        logger.debug("Database ping successful")
        typer.echo("✓ Database connection: OK")
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        typer.echo(f"✗ Database connection failed: {e}", err=True)
        raise typer.Exit(1) from e


@app.command(name="db:init")
def db_init(
    force: bool = typer.Option(False, "--force", "-f", help="Drop and recreate all tables"),
):
    """Initialize database tables."""
    if force:
        typer.echo("Force mode: dropping all tables...")
    init_db(drop_all=force)
    typer.echo("✓ Database initialized successfully")


@app.command(name="db:seed")
def db_seed():
    """Seed reference tables from JSON files."""
    db = get_db()

    logger.info("Seeding database from JSON files")

    results = seed_all_from_json(db)

    typer.echo("✓ Seeded reference tables from JSON files:")
    for table, count in results.items():
        typer.echo(f"  • {table}: {count} records")
        logger.info(f"Seeded {count} records into {table}")


@app.command(name="elections:refresh-dates")
def elections_refresh_dates():
    """Scrape upcoming Landtag/Bundestag dates into election reference rows."""
    from pollingapi.scraper.election_dates import refresh_election_dates

    db = get_db()
    updated = refresh_election_dates(db)
    typer.echo(f"✓ Updated {updated} election date(s)")


@app.command(name="db:tables")
def db_tables():
    """List database tables with row counts."""
    from pollingapi.models import (
        Election,
        Institute,
        Method,
        Party,
        PipelineRun,
        Poll,
        PollResult,
        Provider,
        RawPoll,
        Tasker,
    )

    db = get_db()
    tables = [
        ("polls_raw", RawPoll),
        ("polls", Poll),
        ("poll_results", PollResult),
        ("institutes", Institute),
        ("parties", Party),
        ("providers", Provider),
        ("elections", Election),
        ("methods", Method),
        ("taskers", Tasker),
        ("pipeline_runs", PipelineRun),
    ]

    typer.echo("Table row counts:")
    typer.echo("-" * 40)
    for name, model in tables:
        count = db.query(model).count()
        typer.echo(f"  {name}: {count}")


@app.command(name="export:all")
def db_export():
    """Export data to JSON, CSV, and Parquet files."""
    db = get_db()
    export_service = ExportService(db)
    counts = export_service.export_all()
    typer.echo(f"✓ Exported to {settings.export_dir}:")
    typer.echo(f"  public polls: {counts['polls']}")
    typer.echo(f"  public polls_without_results: {counts['polls_without_results']}")
    typer.echo(f"  public poll_results: {counts['results']}")
    typer.echo(f"  all_cleaned_polls: {counts['all_cleaned_polls']}")
    typer.echo(f"  all_cleaned_poll_results: {counts['all_cleaned_results']}")
    typer.echo(f"  raw_polls: {counts['raw']}")


@app.command(name="services:report")
def services_report(
    run_id: str | None = typer.Option(None, "--run-id", help="Pipeline run id to link"),
):
    """Generate the PDF data report."""
    db = get_db()
    report_service = ReportService(db)
    try:
        report_path = report_service.generate(run_id=run_id)
    except Exception as exc:
        typer.echo(f"✗ Report generation failed: {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"✓ Report generated: {report_path}")
    typer.echo(f"  Latest report : {report_service.latest_report_path()}")


@app.command(name="validation:run")
def validation_run(
    limit: int | None = typer.Option(None, "--limit", "-l", help="Limit polls to validate"),
    show: int = typer.Option(20, "--show", help="Number of invalid/warning polls to print"),
    persist: bool = typer.Option(False, "--persist", help="Write results to poll_validations"),
):
    """Run data validation on cleaned polls."""
    if persist:
        init_db()
    db = get_db()
    service = DataValidationService(db)
    report = service.run(limit=limit, persist=persist)
    summary = report.summary

    typer.echo("Data validation complete:")
    typer.echo(f"  Total polls   : {summary.total_polls}")
    typer.echo(f"  Research-ready polls              : {summary.valid_polls}")
    typer.echo(f"  Polls outside quality criteria    : {summary.invalid_polls}")
    typer.echo(f"  Polls with review notes           : {summary.warning_polls}")
    if persist:
        typer.echo("  Persisted to  : poll_validations")

    flagged = [
        item
        for item in report.items
        if not item.valid
        or not item.qc_institute_result_jump.passed
        or not item.qc_scope_result_jump.passed
    ][:show]
    if flagged:
        typer.echo("")
        typer.echo(f"First {len(flagged)} invalid/warning polls:")
        for item in flagged:
            status = "invalid" if not item.valid else "warning"
            typer.echo(f"  {item.public_id or item.poll_id}: {status}")


@app.command(name="validation:inspect")
def validation_inspect(
    poll_identifier: str = typer.Argument(..., help="Poll id or public id, e.g. 1 or C00000001"),
):
    """Inspect persisted validation for one poll."""
    db = get_db()
    service = DataValidationService(db)
    item = service.get_persisted(poll_identifier)
    if item is None:
        typer.echo(
            "✗ Validation not found. Run: pollingapi validation:run --persist",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"Validation for {item.public_id or item.poll_id}")
    typer.echo(f"  Poll id      : {item.poll_id}")
    typer.echo(f"  Valid        : {item.valid}")
    typer.echo(f"  Validated at : {item.validated_at}")
    typer.echo("")
    typer.echo("Checks:")
    for name in (
        "qc_party_percentage_range",
        "qc_result_sum_check",
        "qc_date_consistency",
        "qc_respondents_plausible",
        "qc_core_parties_present",
        "qc_institute_result_jump",
        "qc_scope_result_jump",
    ):
        check = getattr(item, name)
        status = "pass" if check.passed else check.severity
        typer.echo(f"  {name}: {status}")
        if not check.passed and check.message:
            typer.echo(f"    {check.message}")
        if not check.passed and check.observed is not None:
            typer.echo(f"    observed: {check.observed}")
        if not check.passed and check.affected_parties:
            typer.echo(f"    parties : {', '.join(check.affected_parties)}")


@app.command(name="validation:report")
def validation_report(
    top: int = typer.Option(5, "--top", help="Number of top failure checks to print"),
):
    """Show aggregate report for persisted validation results."""
    db = get_db()
    report = ValidationReportService(db).build_report(top_n=top)

    typer.echo("Validation report:")
    typer.echo(f"  Status        : {report.status}")
    typer.echo(f"  Total polls   : {report.total_polls}")
    typer.echo(
        f"  Research-ready polls           : {report.valid_polls} ({report.valid_share:.1%})"
    )
    typer.echo(
        f"  Polls outside quality criteria : {report.invalid_polls} ({report.invalid_share:.1%})"
    )
    typer.echo(
        f"  Polls with review notes        : {report.warning_polls} ({report.warning_share:.1%})"
    )
    typer.echo(f"  Latest run    : {report.latest_validated_at}")

    typer.echo("")
    typer.echo("Checks:")
    for check in report.checks:
        typer.echo(
            f"  {check.check}: {check.pass_share:.1%} passed "
            f"({check.passed}/{check.passed + check.failed})"
        )

    if report.top_failure_checks:
        typer.echo("")
        typer.echo("Checks needing review:")
        for item in report.top_failure_checks:
            typer.echo(f"  {item.check}: {item.failed}")


@app.command(name="db:reset")
def db_reset(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm destructive operation"),
):
    """Reset database (drop all tables and recreate)."""
    if not confirm:
        typer.echo("⚠️  This will delete all data! Use --confirm to proceed.", err=True)
        raise typer.Exit(1)

    typer.echo("Resetting database...")
    init_db(drop_all=True)
    typer.echo("✓ Database reset successfully")


@app.command(name="scraper:run")
def scraper_run(
    worker: str = typer.Argument(
        ...,
        help="Worker name (e.g., 'forsa', 'bayern', 'all', 'current')",
    ),
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug mode"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Dry run (don't insert to DB)"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force run (ignore initial run markers)"
    ),
):
    """Run a specific scraper worker or all workers."""
    # Reconfigure logging for debug mode if needed
    if debug:
        setup_logging(log_level="DEBUG")

    db = get_db()
    context = RunContext.for_project(debug=debug)

    logger.info(f"Starting scraper run: worker={worker}, dry_run={dry_run}, debug={debug}")

    runner = ScraperRunner(db, context=context, dry_run=dry_run or debug)

    if worker.lower() in {"all", "current"}:
        current_only = worker.lower() == "current"
        logger.info("Running current scrapers" if current_only else "Running all scrapers")
        results = runner.run_all(include_dawum=not current_only, current_only=current_only)

        # Log results
        total_success = sum(1 for v in results.values() if isinstance(v, int))
        total_polls = sum(v for v in results.values() if isinstance(v, int))
        logger.info(f"Scraper run completed: {total_success} successful, {total_polls} total polls")

        typer.echo("\nResults:")
        typer.echo("-" * 50)
        for name, count in results.items():
            if isinstance(count, int):
                typer.echo(f"  ✓ {name}: {count} polls")
                logger.info(f"Scraper {name}: {count} polls inserted")
            else:
                typer.echo(f"  ✗ {name}: {count}")
                logger.error(f"Scraper {name} failed: {count}")
    else:
        logger.info(f"Running scraper: {worker}")
        try:
            count = runner.run_worker(worker)
            message = (
                f"would insert {count} polls" if dry_run or debug else f"inserted {count} polls"
            )
            typer.echo(f"✓ {worker}: {message}")
            logger.info(f"Scraper {worker} completed: {message}")
        except ValueError as e:
            logger.error(f"Scraper {worker} failed: {e}")
            typer.echo(f"✗ Error: {e}", err=True)
            typer.echo("\nAvailable workers:")
            for name in runner.list_workers():
                typer.echo(f"  - {name}")
            raise typer.Exit(1) from e


@app.command(name="scraper:list")
def scraper_list():
    """List all available scraper workers."""
    db = get_db()
    runner = ScraperRunner(db)

    typer.echo("Available scraper workers:")
    typer.echo("-" * 50)
    for name in sorted(runner.list_workers()):
        typer.echo(f"  • {name}")


@app.command(name="scraper:status")
def scraper_status():
    """Show scraper run status and data freshness."""

    typer.echo("Scraper status:")
    typer.echo("-" * 50)

    data_dir = settings.data_dir
    if not data_dir.exists():
        typer.echo("  No data directory found")
        return

    workers = [d.name for d in data_dir.iterdir() if d.is_dir()]
    for worker in sorted(workers):
        marker_file = data_dir / worker / ".historic_urls_processed"
        if marker_file.exists():
            typer.echo(f"  ✓ {worker}: Historic data processed")
        else:
            typer.echo(f"  ○ {worker}: Awaiting initial run")


@app.command(name="import:list")
def import_list():
    """List available data import sources."""
    db = get_db()
    runner = ImportRunner(db)

    typer.echo("Available import sources:")
    typer.echo("-" * 50)
    for name in runner.list_sources():
        typer.echo(f"  • {name}")
    typer.echo("")
    typer.echo(f"Import directory: {IMPORTS_DIR}")


@app.command(name="import:download")
def import_download(
    manifest: ImportManifestOption = DEFAULT_MANIFEST,
    force: bool = typer.Option(False, "--force", "-f", help="Redownload existing files"),
    timeout: float = typer.Option(60.0, "--timeout", help="HTTP timeout in seconds"),
):
    """Download import files declared in the project root manifest."""
    try:
        results = download_from_manifest(manifest_path=manifest, force=force, timeout=timeout)
    except (FileNotFoundError, HTTPError, ValueError) as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Import downloads from {manifest}:")
    if not results:
        typer.echo("  No URLs configured")
        return

    for result in results:
        status = "downloaded" if result.downloaded else "skipped"
        typer.echo(f"  {status}: {result.destination} ({result.bytes_written} bytes)")


@app.command(name="import:preview")
def import_preview(
    file: ImportFileArg,
    source: ImportSourceOption = "csv",
    limit: ImportPreviewLimitOption = 10,
):
    """Preview parsed import rows without writing to the database."""
    db = get_db()
    runner = ImportRunner(db)

    try:
        rows = runner.preview(source, file, limit=limit)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Previewing {len(rows)} row(s):")
    for index, row in enumerate(rows, start=1):
        data = row.to_raw_dict()
        typer.echo(f"  {index}. {data['publish_date']} | {data['institute_id']} | {data['scope']}")
        typer.echo(f"     parties: {data['parties']}")


@app.command(name="import:run")
def import_run(
    file: ImportFileArg,
    source: ImportSourceOption = "csv",
    clean: bool = typer.Option(False, "--clean", help="Run cleaner after importing"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Parse and dedupe without committing"
    ),
):
    """Import file data into polls_raw."""
    db = get_db()
    runner = ImportRunner(db)

    try:
        result = runner.run(source, file, clean=clean, dry_run=dry_run)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"✗ {exc}", err=True)
        raise typer.Exit(1) from exc

    if result.stats.errors:
        typer.echo("✗ Import failed:", err=True)
        for message in result.stats.error_messages:
            typer.echo(f"  {message}", err=True)
        raise typer.Exit(1)

    action = "Would import" if dry_run else "Imported"
    typer.echo(f"✓ {action} data from {result.path}:")
    typer.echo(f"  Parsed: {result.stats.parsed}")
    typer.echo(f"  Inserted: {result.stats.inserted}")
    typer.echo(f"  Skipped: {result.stats.skipped}")

    if result.cleaning_stats:
        typer.echo("  Cleaner:")
        typer.echo(f"    Processed: {result.cleaning_stats['processed']}")
        typer.echo(f"    Created: {result.cleaning_stats['created']}")
        typer.echo(f"    Updated: {result.cleaning_stats['updated']}")
        typer.echo(f"    Skipped: {result.cleaning_stats['skipped']}")
        typer.echo(f"    Errors: {result.cleaning_stats['errors']}")


@app.command(name="pipeline:clean")
def pipeline_clean(
    limit: int | None = typer.Option(None, "--limit", "-l", help="Limit number of rows to process"),
    reprocess: bool = typer.Option(False, "--reprocess", help="Reprocess already-cleaned rows"),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Delete cleaned poll/reference rows and rebuild from immutable raw rows",
    ),
):
    """Run data cleaning pipeline on raw polls."""
    db = get_db()
    stats = run_cleaning_pipeline(db, limit=limit, reprocess=reprocess, rebuild=rebuild)

    typer.echo("✓ Cleaning complete:")
    typer.echo(f"  Processed: {stats['processed']}")
    typer.echo(f"  Created: {stats['created']}")
    typer.echo(f"  Updated: {stats['updated']}")
    typer.echo(f"  Skipped: {stats['skipped']}")
    typer.echo(f"  Errors: {stats['errors']}")
    typer.echo(f"  Matched pairs: {stats['matched_pairs']}")
    typer.echo(f"  Multiple matches: {stats['multiple_matches']}")


@app.command(name="pipeline:run")
def pipeline_run(
    include_dawum: bool = typer.Option(True, "--dawum/--no-dawum", help="Include DAWUM API"),
):
    """Run full pipeline (scraper + cleaner + export + archive)."""
    import shutil
    from datetime import datetime

    from pollingapi.models import PipelineRun

    db = get_db()
    s3_service = S3Service()

    # ------------------------------------------------------------------ setup
    run_result = PipelineRunResult()
    run_result.started_at = datetime.now()
    notifier = create_notification_manager()

    try:
        # -------------------------------------------------------------- scraper
        typer.echo("=== Running Scraper ===")
        typer.echo("")
        context = RunContext.for_project(run_id=run_result.run_id)
        runner = ScraperRunner(db, context=context)
        scraper_results = runner.run_all(include_dawum=include_dawum)

        for name, value in scraper_results.items():
            run_result.scrapers_run += 1
            if isinstance(value, int):
                run_result.scrapers_succeeded += 1
                run_result.total_scraped_polls += value
            else:
                run_result.scrapers_failed += 1
                run_result.scraper_errors[name] = str(value)
        run_result.zero_poll_workers = runner.zero_poll_workers

        typer.echo(f"✓ Total scraped: {run_result.total_scraped_polls} polls")
        typer.echo(
            f"  Workers: {run_result.scrapers_succeeded} OK / {run_result.scrapers_failed} failed"
        )
        if run_result.scraper_errors:
            for name, err in run_result.scraper_errors.items():
                typer.echo(f"  ✗ {name}: {err}")
        if run_result.zero_poll_workers:
            typer.echo("  ⚠ Zero-poll warnings:")
            for name in run_result.zero_poll_workers:
                typer.echo(f"    {name}: found no polls, but previous raw polls exist")
        typer.echo("")

        # -------------------------------------------------------------- election dates
        typer.echo("=== Refreshing election dates ===")
        typer.echo("")
        from pollingapi.scraper.election_dates import refresh_election_dates

        try:
            updated_dates = refresh_election_dates(db)
            typer.echo(f"✓ Updated {updated_dates} election date(s)")
        except Exception as exc:
            logger.warning("Election date refresh failed: %s", exc)
            typer.echo(f"⚠ Election date refresh failed: {exc}")
        typer.echo("")

        # -------------------------------------------------------------- cleaner
        typer.echo("=== Running Cleaner ===")
        typer.echo("")
        etl_stats = run_cleaning_pipeline(db)
        run_result.etl_processed = etl_stats["processed"]
        run_result.etl_created = etl_stats["created"]
        run_result.etl_updated = etl_stats["updated"]
        run_result.etl_skipped = etl_stats["skipped"]
        run_result.etl_errors = etl_stats["errors"]

        typer.echo(f"✓ Processed : {run_result.etl_processed}")
        typer.echo(f"✓ Created   : {run_result.etl_created}")
        typer.echo(f"✓ Updated   : {run_result.etl_updated}")
        typer.echo(f"✓ Skipped   : {run_result.etl_skipped}")
        typer.echo(f"✓ Errors    : {run_result.etl_errors}")
        typer.echo(f"✓ Matches   : {etl_stats['matched_pairs']}")
        typer.echo(f"✓ Ambiguous : {etl_stats['multiple_matches']}")
        typer.echo("")

        # -------------------------------------------------------------- validation
        typer.echo("=== Running Validation ===")
        typer.echo("")
        validation_service = DataValidationService(db)
        validation_service.run(persist=True)
        validation_report = ValidationReportService(db).build_report(top_n=3)
        run_result.validation_status = validation_report.status
        run_result.validation_total_polls = validation_report.total_polls
        run_result.validation_valid_polls = validation_report.valid_polls
        run_result.validation_invalid_polls = validation_report.invalid_polls
        run_result.validation_warning_polls = validation_report.warning_polls
        run_result.validation_valid_share = validation_report.valid_share
        run_result.validation_top_failures = [
            item.model_dump(mode="json") for item in validation_report.top_failure_checks
        ]

        typer.echo(f"✓ Status    : {run_result.validation_status}")
        typer.echo(
            f"✓ Valid     : {run_result.validation_valid_polls}/"
            f"{run_result.validation_total_polls}"
            f" ({run_result.validation_valid_share:.1%})"
            if run_result.validation_valid_share is not None
            else f"✓ Valid     : {run_result.validation_valid_polls}/"
            f"{run_result.validation_total_polls}"
        )
        typer.echo(f"✓ Outside quality criteria: {run_result.validation_invalid_polls}")
        typer.echo(f"✓ Review notes            : {run_result.validation_warning_polls}")
        if run_result.validation_top_failures:
            typer.echo("  Checks needing review:")
            for item in run_result.validation_top_failures:
                typer.echo(f"    {item['check']}: {item['failed']}")
        typer.echo("")

        # -------------------------------------------------------------- export
        typer.echo("=== Running Export ===")
        typer.echo("")
        export_service = ExportService(db)
        export_counts = export_service.export_all()
        run_result.export_polls = export_counts["polls"]
        run_result.export_poll_results = export_counts["results"]
        run_result.export_raw_polls = export_counts["raw"]

        typer.echo(
            f"✓ Exported {run_result.export_polls} polls,"
            f" {export_counts['polls_without_results']} polls without results,"
            f" {run_result.export_poll_results} poll results,"
            f" {export_counts['all_cleaned_polls']} all-cleaned polls,"
            f" and {run_result.export_raw_polls} raw polls"
        )
        typer.echo("")

        # -------------------------------------------------------------- archive
        if s3_service.is_configured():
            typer.echo("=== Creating Archive ===")
            typer.echo("")

            archive_name = f"pollingapi-archive-{datetime.now().strftime('%Y-%m-%d-%H-%M')}.zip"
            archive_path = settings.data_dir.parent / archive_name

            import tempfile

            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                _stage_archive_bundle(tmp_path)

                shutil.make_archive(
                    base_name=str(archive_path.with_suffix("")),
                    format="zip",
                    root_dir=str(tmp_path),
                )

            archive_size = archive_path.stat().st_size
            archive_size_mb = archive_size / 1024 / 1024
            typer.echo(f"✓ Created archive: {archive_name} ({archive_size_mb:.1f} MB)")

            typer.echo(f"Uploading to S3 bucket: {s3_service.bucket_name}...")

            key = f"archives/{archive_name}"
            if s3_service.upload_archive(archive_path, key):
                typer.echo(f"✓ Uploaded to s3://{s3_service.bucket_name}/{key}")

                archives = s3_service.list_archives()
                s3_service.upload_index(archives)
                typer.echo("✓ Updated archive index")

                archive_path.unlink()
                typer.echo(f"✓ Removed local archive: {archive_name}")

                run_result.archive_created = True
                run_result.archive_size_mb = archive_size_mb
            else:
                typer.echo("✗ Failed to upload to S3", err=True)

            typer.echo("")
            typer.echo("=== Archive Complete ===")
        else:
            typer.echo("=== Skipping Archive (S3 not configured) ===")
            typer.echo("")

        run_result.success = True

    except Exception as exc:
        run_result.success = False
        run_result.error = str(exc)
        logger.error(f"Pipeline run failed: {exc}", exc_info=True)
        typer.echo(f"\n✗ Pipeline failed: {exc}", err=True)

    finally:
        run_result.finished_at = datetime.now()

        # ---------------------------------------------------------- persist run record
        try:
            pipeline_run_record = PipelineRun(
                run_id=run_result.run_id,
                started_at=run_result.started_at,
                finished_at=run_result.finished_at,
                duration_seconds=run_result.duration_seconds,
                success=run_result.success,
                error=run_result.error,
                scrapers_run=run_result.scrapers_run,
                scrapers_succeeded=run_result.scrapers_succeeded,
                scrapers_failed=run_result.scrapers_failed,
                total_scraped_polls=run_result.total_scraped_polls,
                etl_processed=run_result.etl_processed,
                etl_created=run_result.etl_created,
                etl_updated=run_result.etl_updated,
                etl_skipped=run_result.etl_skipped,
                etl_errors=run_result.etl_errors,
                export_polls=run_result.export_polls,
                export_poll_results=run_result.export_poll_results,
                export_raw_polls=run_result.export_raw_polls,
                validation_status=run_result.validation_status,
                validation_total_polls=run_result.validation_total_polls,
                validation_valid_polls=run_result.validation_valid_polls,
                validation_invalid_polls=run_result.validation_invalid_polls,
                validation_warning_polls=run_result.validation_warning_polls,
                validation_valid_share=run_result.validation_valid_share,
                archive_created=run_result.archive_created,
                archive_size_mb=run_result.archive_size_mb,
            )
            db.add(pipeline_run_record)
            db.commit()
            logger.info(f"Pipeline run record saved: run_id={run_result.run_id}")
        except Exception as db_exc:
            logger.warning(f"Failed to persist pipeline run record: {db_exc}")

        # ---------------------------------------------------------- generate report
        try:
            report_path = ReportService(db).generate(run_id=run_result.run_id)
            logger.info(f"Pipeline report generated: {report_path}")
        except Exception as report_exc:
            logger.warning(f"Failed to generate pipeline report: {report_exc}")

        # ---------------------------------------------------------- notify
        notifier.notify(run_result)

        # ---------------------------------------------------------- summary
        status_icon = "✓" if run_result.success else "✗"
        typer.echo(f"=== Pipeline {'Complete' if run_result.success else 'FAILED'} ===")
        typer.echo("")
        typer.echo(f"  {status_icon} Run ID  : {run_result.run_id}")
        typer.echo(f"    Duration: {run_result.duration_human}")
        typer.echo(f"    Scraped : {run_result.total_scraped_polls} new polls")
        typer.echo(
            f"    Created : {run_result.etl_created} | Updated: {run_result.etl_updated}"
            f" | Errors: {run_result.etl_errors}"
        )
        if run_result.validation_status:
            valid_share = (
                f"{run_result.validation_valid_share:.1%}"
                if run_result.validation_valid_share is not None
                else "n/a"
            )
            typer.echo(f"    QC      : {run_result.validation_status} | Valid: {valid_share}")
        if notifier.notifier_count > 0:
            typer.echo(f"    Notified: {notifier.notifier_count} backend(s)")
        if run_result.zero_poll_workers:
            typer.echo(f"    Warning : {len(run_result.zero_poll_workers)} zero-poll worker(s)")
        typer.echo("")

        if not run_result.success:
            raise typer.Exit(1)


@app.command(name="pipeline:inspect")
def pipeline_inspect(
    raw_id: int = typer.Argument(..., help="Raw poll ID to inspect"),
):
    """Inspect how a single raw row would be cleaned."""
    db = get_db()
    from pollingapi.models import RawPoll

    raw_poll = db.query(RawPoll).filter(RawPoll.id == raw_id).first()
    if not raw_poll:
        typer.echo(f"✗ Raw poll {raw_id} not found", err=True)
        raise typer.Exit(1)

    typer.echo(f"Inspecting raw poll {raw_id}:")
    typer.echo(f"  publish_date: {raw_poll.publish_date}")
    typer.echo(f"  institute_id: {raw_poll.institute_id}")
    typer.echo(f"  provider: {raw_poll.provider}")
    typer.echo(f"  scope: {raw_poll.scope}")
    typer.echo(f"  parties: {raw_poll.parties}")


# ============================================================================
# Server Commands (server:*)
# ============================================================================


@app.command(name="server:start")
def server_start(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind to"),
    reload: bool = typer.Option(False, "--reload", "-r", help="Enable auto-reload"),
):
    """Start the API server (development mode)."""
    import uvicorn

    logger.info(f"Starting API server on {host}:{port}")
    typer.echo(f"Starting server on {host}:{port}...")
    uvicorn.run(
        "pollingapi.main:app",
        host=host,
        port=port,
        reload=reload,
    )


@app.command(name="server:prod")
def server_prod(
    host: str = typer.Option(
        "127.0.0.1", "--host", "-h", help="Host to bind to (use 127.0.0.1 for nginx reverse proxy)"
    ),
    port: int = typer.Option(8000, "--port", "-p", help="Port to bind to"),
    workers: int | None = typer.Option(
        None, "--workers", "-w", help="Number of Gunicorn workers (default: 2 * CPU cores + 1)"
    ),
    timeout: int = typer.Option(120, "--timeout", "-t", help="Worker timeout in seconds"),
    keepalive: int = typer.Option(5, "--keepalive", help="Keep-alive timeout in seconds"),
    max_requests: int = typer.Option(
        10000,
        "--max-requests",
        help="Max requests per worker before restart (prevents memory leaks)",
    ),
    access_log: str | None = typer.Option(
        None, "--access-log", help="Access log file path (default: stdout)"
    ),
    error_log: str | None = typer.Option(
        None, "--error-log", help="Error log file path (default: stderr)"
    ),
    daemon: bool = typer.Option(False, "--daemon", "-d", help="Run as daemon (background process)"),
    pid_file: str | None = typer.Option(None, "--pid", help="PID file path for daemon mode"),
):
    """Start the production API server with Gunicorn.

    This is the recommended way to run in production with nginx as reverse proxy.
    Bind to 127.0.0.1 and let nginx handle external traffic.

    Examples:
        # Basic production server (binds to localhost:8000, 5 workers)
        pollingapi server:prod

        # Custom workers and port
        pollingapi server:prod -h 127.0.0.1 -p 8080 -w 4

        # With log files
        pollingapi server:prod --access-log /var/log/pollingapi/access.log \\
                              --error-log /var/log/pollingapi/error.log

        # As daemon with PID file
        pollingapi server:prod --daemon --pid /var/run/pollingapi.pid
    """
    import multiprocessing
    import subprocess
    import sys

    # Calculate default workers if not specified
    if workers is None:
        workers = (multiprocessing.cpu_count() * 2) + 1

    # Ensure logs directory exists if log files specified
    for log_path in [access_log, error_log]:
        if log_path:
            log_dir = Path(log_path).parent
            log_dir.mkdir(parents=True, exist_ok=True)

    # Build Gunicorn command
    cmd = [
        sys.executable,
        "-m",
        "gunicorn",
        "-k",
        "uvicorn.workers.UvicornWorker",
        "pollingapi.main:app",
        "--bind",
        f"{host}:{port}",
        "--workers",
        str(workers),
        "--timeout",
        str(timeout),
        "--keep-alive",
        str(keepalive),
        "--max-requests",
        str(max_requests),
        "--max-requests-jitter",
        str(max_requests // 20),  # 5% jitter
        "--worker-class",
        "uvicorn.workers.UvicornWorker",
        "--worker-tmp-dir",
        "/dev/shm",  # Use RAM for temp files (faster)
        "--preload",  # Preload app for memory efficiency
    ]

    # Add logging options
    if access_log:
        cmd.extend(["--access-logfile", access_log])
    else:
        cmd.append("--access-logfile")  # Send to stdout
        cmd.append("-")

    if error_log:
        cmd.extend(["--error-logfile", error_log])
    else:
        cmd.append("--error-logfile")  # Send to stderr
        cmd.append("-")

    # Add daemon options
    if daemon:
        cmd.append("--daemon")
        if pid_file:
            cmd.extend(["--pid", pid_file])

    # Log configuration
    logger.info(
        f"Starting production server: host={host}, port={port}, workers={workers}, "
        f"timeout={timeout}s, max_requests={max_requests}"
    )

    typer.echo("Starting production server with Gunicorn...")
    typer.echo(f"  Bind: {host}:{port}")
    typer.echo(f"  Workers: {workers}")
    typer.echo(f"  Timeout: {timeout}s")
    typer.echo(f"  Max requests/worker: {max_requests}")

    if host == "0.0.0.0":
        typer.echo("")
        typer.echo("⚠️  Warning: Binding to 0.0.0.0 exposes the server directly to the internet.")
        typer.echo("   Consider using 127.0.0.1 with nginx as reverse proxy for production.")
    elif host == "127.0.0.1":
        typer.echo("")
        typer.echo("✓ Binding to localhost (127.0.0.1)")
        typer.echo("  Ensure nginx is configured as reverse proxy:")
        typer.echo("")
        typer.echo(r"  location / {")
        typer.echo(r"      proxy_pass http://127.0.0.1:8000;")
        typer.echo(r"      proxy_set_header Host $host;")
        typer.echo(r"      proxy_set_header X-Real-IP $remote_addr;")
        typer.echo(r"  }")

    if daemon:
        typer.echo("")
        typer.echo("Running as daemon")
        if pid_file:
            typer.echo(f"PID file: {pid_file}")

    typer.echo("")

    try:
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            logger.error(f"Gunicorn exited with code {result.returncode}")
            raise typer.Exit(result.returncode)
    except KeyboardInterrupt:
        typer.echo("\nShutting down server...")
        logger.info("Server shutdown requested via keyboard interrupt")
        raise typer.Exit(0) from None
    except Exception as e:
        logger.error(f"Failed to start server: {e}")
        typer.echo(f"✗ Error starting server: {e}", err=True)
        raise typer.Exit(1) from e


# ============================================================================
# Log Commands (logs:*)
# ============================================================================


@app.command(name="logs:view")
def logs_view(
    log_file: str = typer.Option(
        "zweitstimme", "--file", "-f", help="Log file to view (zweitstimme, scraper, errors)"
    ),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", "-F", help="Follow log output (like tail -f)"),
):
    """View log files."""
    log_dir = settings.data_dir / "logs"

    if log_file == "zweitstimme":
        log_path = log_dir / "zweitstimme.log"
    elif log_file == "scraper":
        log_path = log_dir / "scraper.log"
    elif log_file == "errors":
        log_path = log_dir / "errors.log"
    else:
        log_path = log_dir / log_file

    if not log_path.exists():
        typer.echo(f"✗ Log file not found: {log_path}", err=True)
        raise typer.Exit(1)

    if follow:
        typer.echo(f"Following {log_path} (Ctrl+C to exit)...")
        import time

        with open(log_path) as f:
            # Go to end of file
            f.seek(0, 2)
            try:
                while True:
                    line = f.readline()
                    if line:
                        typer.echo(line.rstrip())
                    else:
                        time.sleep(0.1)
            except KeyboardInterrupt:
                typer.echo("\nStopped following logs.")
    else:
        # Read last N lines
        with open(log_path) as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            typer.echo(f"Last {len(last_lines)} lines from {log_path}:")
            typer.echo("-" * 60)
            for line in last_lines:
                typer.echo(line.rstrip())


@app.command(name="logs:list")
def logs_list():
    """List available log files."""
    log_dir = settings.data_dir / "logs"

    if not log_dir.exists():
        typer.echo("No logs directory found.")
        return

    typer.echo("Available log files:")
    typer.echo("-" * 50)

    log_files = ["zweitstimme.log", "scraper.log", "errors.log"]
    for log_file in log_files:
        log_path = log_dir / log_file
        if log_path.exists():
            size = log_path.stat().st_size
            size_str = f"{size / 1024:.1f} KB" if size > 1024 else f"{size} B"
            typer.echo(f"  ✓ {log_file}: {size_str}")
        else:
            typer.echo(f"  ○ {log_file}: not created yet")


# ============================================================================
# Data Archive Commands (data:*)
# ============================================================================


@app.command(name="data:archive")
def data_archive(
    keep: bool = typer.Option(False, "--keep", help="Keep local archive after upload"),
):
    """Create and upload data archive to S3."""
    import shutil
    from datetime import datetime

    s3_service = S3Service()

    if not s3_service.is_configured():
        typer.echo("✗ S3 not configured. Check AWS environment variables.", err=True)
        typer.echo("\nRequired environment variables:")
        typer.echo("  AWS_ACCESS_KEY_ID")
        typer.echo("  AWS_SECRET_ACCESS_KEY")
        typer.echo("  AWS_S3_BUCKET_NAME")
        typer.echo("  AWS_S3_REGION")
        typer.echo("  AWS_S3_ENDPOINT_URL (for S3-compatible services)")
        raise typer.Exit(1)

    typer.echo("Running data export...")

    db = get_db()
    export_service = ExportService(db)
    export_counts = export_service.export_all()
    typer.echo(
        f"✓ Exported {export_counts['polls']} public polls, "
        f"{export_counts['polls_without_results']} public polls without results, "
        f"{export_counts['all_cleaned_polls']} all-cleaned polls, "
        f"and {export_counts['raw']} raw polls"
    )

    typer.echo("\nCreating data archive...")

    if not settings.export_dir.exists():
        typer.echo(f"✗ Export directory not found: {settings.export_dir}", err=True)
        raise typer.Exit(1)

    archive_name = f"pollingapi-archive-{datetime.now().strftime('%Y-%m-%d-%H-%M')}.zip"
    archive_path = settings.data_dir.parent / archive_name

    try:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)

            _stage_archive_bundle(tmp_path)

            shutil.make_archive(
                base_name=str(archive_path.with_suffix("")),
                format="zip",
                root_dir=str(tmp_path),
            )

        archive_size = archive_path.stat().st_size
        typer.echo(f"✓ Created archive: {archive_name} ({archive_size / 1024 / 1024:.1f} MB)")

        typer.echo(f"Uploading to S3 bucket: {s3_service.bucket_name}...")

        key = f"archives/{archive_name}"
        if s3_service.upload_archive(archive_path, key):
            typer.echo(f"✓ Uploaded to s3://{s3_service.bucket_name}/{key}")

            archives = s3_service.list_archives()
            s3_service.upload_index(archives)
            typer.echo("✓ Updated archive index")

            if not keep:
                archive_path.unlink()
                typer.echo(f"✓ Removed local archive: {archive_name}")
            else:
                typer.echo(f"✓ Kept local archive: {archive_path}")

            typer.echo("\nArchive available at:")
            typer.echo("  /v1/archive")
            typer.echo("  /v1/archive.json")
        else:
            typer.echo("✗ Failed to upload to S3", err=True)
            raise typer.Exit(1)

    except Exception as e:
        logger.error(f"Failed to create archive: {e}")
        typer.echo(f"✗ Error: {e}", err=True)
        if archive_path.exists():
            archive_path.unlink()
        raise typer.Exit(1) from e


@app.command(name="data:list")
def data_list():
    """List available data archives in S3."""
    s3_service = S3Service()

    if not s3_service.is_configured():
        typer.echo("✗ S3 not configured.", err=True)
        raise typer.Exit(1)

    archives = s3_service.list_archives()

    if not archives:
        typer.echo("No archives found in S3 bucket.")
        return

    typer.echo(f"Available archives in {s3_service.bucket_name}:")
    typer.echo("-" * 60)
    for archive in archives:
        size_mb = archive.size / 1024 / 1024
        date_str = archive.created_at.strftime("%Y-%m-%d %H:%M")
        typer.echo(f"  {archive.filename}")
        typer.echo(f"    Size: {size_mb:.1f} MB | Date: {date_str}")
        typer.echo(f"    URL: {archive.public_url}")


def main():
    """Main entry point."""
    app()


if __name__ == "__main__":
    main()
