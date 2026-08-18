# pollingAPI

German election polling data pipeline and API for zweitstimme.org.

The project collects polling data, keeps immutable raw source rows, cleans them
into a relational model, validates data quality, exports research datasets, and
serves the public dataset through FastAPI.

## Requirements

- Python 3.12 or newer
- `uv`
- `just` is optional, but useful for common commands

## Quick Start

```bash
uv sync
uv run pollingapi db:init
uv run pollingapi db:seed
uv run pollingapi pipeline:run
uv run pollingapi server:start --reload
```

Open:

- API docs: `http://localhost:8000/docs`
- OpenAPI: `http://localhost:8000/openapi.json`
- Health: `http://localhost:8000/health`

## Data Flow

```text
scrapers + imports
        |
        v
polls_raw
        |
        v
cleaning pipeline
        |
        v
polls + poll_results + reference tables
        |
        v
validation + public policy
        |
        +--> public API
        +--> export files
        +--> archive bundle and optional S3 upload
```

`polls_raw` is the immutable source table. Cleaned and normalized data is stored
in `polls` and `poll_results`.

## Main Commands

Use the CLI directly:

```bash
uv run pollingapi --help
```

Or use `just`:

```bash
just help
```

### Database

```bash
uv run pollingapi db:init
uv run pollingapi db:seed
uv run pollingapi db:tables
uv run pollingapi db:ping
```

### Imports

Historical Kayser/Rehmert data is configured in `import_urls.txt`.

```bash
uv run pollingapi import:download
uv run pollingapi import:run KAYSER_REHMERT.xlsx --source kayser_rehmert
```

### Scraping And Cleaning

```bash
uv run pollingapi scraper:list
uv run pollingapi scraper:run all
uv run pollingapi pipeline:clean
```

### Full Pipeline

```bash
uv run pollingapi pipeline:run
```

This runs:

```text
scrape -> election dates -> clean -> validate -> export -> report -> archive if S3 is configured
```

Upcoming Landtag dates are scraped from wahlrecht.de on each `pipeline:run`.
Refresh them alone with:

```bash
uv run pollingapi elections:refresh-dates
```

Use this for scheduled production runs after the database is initialized and
historical imports are loaded.

### Validation

```bash
uv run pollingapi policy:validate
uv run pollingapi validation:run --persist
uv run pollingapi validation:inspect C00000001
uv run pollingapi validation:report
```

Validation settings are in `validation.toml`.

Public dataset rules are in `public_policy.yaml`. The public policy controls:

- which validation checks are required for public serving
- source priority before and after 2005
- how secondary-source polls are handled
- contextual core-party presence rules

The contextual core-party rule blocks a poll only when a missing monitored party
is usually present in nearby polls for the same scope. This catches likely
single-poll extraction errors without removing valid state-election periods
where a party is consistently absent.

### Exports

```bash
uv run pollingapi export:all
```

Default export files in `data/export/` contain the same public dataset served by
the API:

- `polls.*`
- `polls_without_results.*`
- `poll_results.*`

Archive export files contain complete cleaned and raw data for audit use:

- `all_cleaned_polls.*`
- `all_cleaned_poll_results.*`
- `polls_raw.*`

Supported formats are JSON, CSV, and Parquet.

### Server

```bash
uv run pollingapi server:start --host 0.0.0.0 --port 8000 --reload
uv run pollingapi server:prod --host 127.0.0.1 --port 8000
```

## API

The public API is `/v2`. Legacy `/v1` routes remain callable for compatibility
but are hidden from OpenAPI.

Important v2 routes:

- `GET /v2/polls`
- `GET /v2/polls/{poll_id}`
- `GET /v2/poll-results`
- `GET /v2/datasets`
- `GET /v2/datasets/default/polls`
- `GET /v2/datasets/all-cleaned/polls`
- `GET /v2/raw-polls`
- `GET /v2/parties`
- `GET /v2/institutes`
- `GET /v2/providers`
- `GET /v2/survey-methods`
- `GET /v2/scopes`
- `GET /v2/elections` — includes `date` / `year` for the next vote in that scope
- `GET /v2/validation-reports/summary`
- `GET /v2/downloads`
- `GET /v2/archives`

The default public poll endpoints use English public names. Internal database
keys are kept unchanged.

## Configuration

The app reads `.env` if the file exists.

Common settings:

- `DATABASE_URL`
- `ASYNC_DATABASE_URL`
- `API_HOST`
- `API_PORT`
- `SCRAPER_DELAY`
- `SCRAPER_TIMEOUT`
- `FEDERAL_ELECTION_DATE` (ISO date, default `2029-02-25`)
- `FEDERAL_ELECTION_DATE_IS_ESTIMATED` (default `true`)
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_S3_BUCKET_NAME`
- `AWS_S3_REGION`
- `AWS_S3_ENDPOINT_URL`
- `NTFY_URL`
- `SLACK_WEBHOOK_URL`

S3 settings are optional. If S3 is not configured, `pipeline:run` still runs and
skips archive upload.

## Project Layout

```text
src/pollingapi/
├── api/              FastAPI routes
├── cleaner/          ETL pipeline and transforms
├── data_validation/  quality checks and reports
├── importer/         file imports, including Kayser/Rehmert
├── scraper/          scraper runner, DAWUM, and HTML workers
├── services/         export, report, and S3 services
├── cli.py            Typer CLI
├── database.py       database setup
├── models.py         SQLAlchemy models
└── schemas.py        Pydantic schemas
```

Top-level data/config files:

- `public_policy.yaml`
- `validation.toml`
- `import_urls.txt`
- `json/`
- `data/`
- `justfile`

## Development

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run pytest tests/
```

Run a focused test:

```bash
uv run pytest tests/test_data_validation.py
```

## Notes

- `pollingapi` and `zweitstimme` are equivalent CLI entrypoints.
- Raw source rows stay in `polls_raw`.
- Validation writes to `poll_validations`.
- Public API/export data is selected by both `is_public` and
  `public_policy.yaml`.
- Complete archive exports are kept for audit and reproducibility.
