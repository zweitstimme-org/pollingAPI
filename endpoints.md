# API Endpoints

This API has two versions:

- `/v2` is the public API for new integrations.
- `/v1` is still available for compatibility, but it is hidden from the public OpenAPI docs.

The docs at `/docs` describe the v2 API only. The old v1 routes still respond, but new clients should use v2.

## How To Think About The API

The API separates four different kinds of data:

1. **Polls** are cleaned poll-level records.
2. **Poll results** are party-level percentages inside polls, usually one row per poll and party.
3. **Datasets** are named views over the polling data. The default dataset is the validated public dataset.
4. **Raw polls** are the original scraper/import rows kept for traceability.

The most important endpoints for normal use are:

```text
GET /v2/polls
GET /v2/poll-results
GET /v2/datasets
GET /v2/datasets/{dataset_key}/polls
GET /v2/datasets/{dataset_key}/poll-results
```

## Response Shape

List endpoints use the same response envelope:

```json
{
  "data": [],
  "pagination": {
    "limit": 100,
    "offset": 0,
    "total": 19725,
    "has_next": true
  },
  "links": {
    "self": "http://localhost:8000/v2/polls?limit=100&offset=0",
    "next": "http://localhost:8000/v2/polls?limit=100&offset=100"
  }
}
```

This keeps pagination predictable across polls, poll results, raw polls, and validation reports.

## Common Query Parameters

Most list endpoints support some combination of these parameters:

| Parameter | Meaning | Example |
|---|---|---|
| `limit` | Maximum number of rows to return | `limit=100` |
| `offset` | Number of rows to skip | `offset=200` |
| `sort` | Sort order | `sort=-published_date` |
| `published_from` | Poll publish date lower bound | `published_from=2024-01-01` |
| `published_to` | Poll publish date upper bound | `published_to=2024-12-31` |
| `scope` | Canonical scope code | `scope=federal` |
| `election_key` | Election key | `election_key=BUND` |
| `institute_key` | Institute key | `institute_key=INSA` |
| `provider_id` | Provider ID | `provider_id=2` |
| `provider_name` | Provider name | `provider_name=DAWUM` |
| `survey_method_key` | Survey method key | `survey_method_key=ONLINE` |
| `party_key` | Party key, for result endpoints | `party_key=CDU_CSU` |
| `source` | Source type | `source=api` |

For filters that accept multiple values, repeat the parameter:

```text
GET /v2/polls?scope=federal&scope=nrw&institute_key=INSA&institute_key=FORSA
```

## Polls

### `GET /v2/polls`

Returns the default public poll dataset.

The default dataset contains polls with persisted validation results that pass the public dataset policy. That policy is configured in `validation.toml` under `[public_dataset]`, so validation cutoffs and inclusion rules can change without changing endpoint code. Researchers can use `/v2/polls` as the normal entry point without needing to remember a quality flag.

Useful examples:

```text
GET /v2/polls?limit=100
GET /v2/polls?scope=federal&limit=50
GET /v2/polls?published_from=2024-01-01&published_to=2024-12-31
GET /v2/polls?institute_key=INSA&sort=-published_date
```

By default, poll records include nested party results. Use `include_results=false` when only poll metadata is needed:

```text
GET /v2/polls?include_results=false
```

Poll records use English public field names, for example:

```json
{
  "public_id": "C00014337",
  "raw_poll_public_id": "R00014382",
  "published_date": "2024-06-01",
  "survey_start_date": "2024-05-25",
  "survey_end_date": "2024-05-30",
  "survey_method_key": "ONLINE",
  "results": []
}
```

### `GET /v2/polls/{poll_id}`

Returns one poll from the validated public dataset.

The preferred identifier is the public poll ID, for example:

```text
GET /v2/polls/C00014337
```

Numeric database IDs also work, but public IDs are better for external users because they are easier to recognize.

### `GET /v2/polls/{poll_id}/results`

Returns only the party results for one poll:

```text
GET /v2/polls/C00014337/results
```

### `GET /v2/polls/{poll_id}/validation-report`

Returns the persisted validation report for one poll, if available:

```text
GET /v2/polls/C00014337/validation-report
```

## Poll Results

### `GET /v2/poll-results`

Returns long-format poll result rows from the validated public dataset. Each row is one party result in one poll.

This is the best endpoint for researchers using R, Python, Stata, spreadsheets, or other analysis tools.

Examples:

```text
GET /v2/poll-results?limit=1000
GET /v2/poll-results?party_key=CDU_CSU
GET /v2/poll-results?scope=federal&party_key=SPD
GET /v2/poll-results?published_from=2024-01-01
```

Use this endpoint when the question is about party vote shares across many polls.

## Datasets

Datasets are named views over the same polling data.

### `GET /v2/datasets`

Lists available datasets.

Current datasets:

| Dataset | Meaning |
|---|---|
| `default` | Validated public polling dataset. Inclusion rules are configured in `validation.toml`. |
| `all-cleaned` | All cleaned polls before public dataset validation filtering. |

### `GET /v2/datasets/{dataset_key}`

Returns metadata for one dataset:

```text
GET /v2/datasets/default
GET /v2/datasets/all-cleaned
```

### `GET /v2/datasets/{dataset_key}/polls`

Returns poll records from a specific dataset:

```text
GET /v2/datasets/default/polls
GET /v2/datasets/all-cleaned/polls
```

This endpoint also supports wide output:

```text
GET /v2/datasets/default/polls?format=wide
```

Wide output returns one row per poll, with party percentages collected in a results dictionary.

### `GET /v2/datasets/{dataset_key}/poll-results`

Returns long-format poll results from a specific dataset:

```text
GET /v2/datasets/default/poll-results
GET /v2/datasets/all-cleaned/poll-results
```

This is the most explicit research endpoint because it makes the chosen dataset visible in the URL.

## Public Dataset Configuration

The default dataset is controlled by:

```toml
[public_dataset]
require_persisted_validation = true
include_valid = true
include_warnings = true
exclude_failed_checks = []
```

With the default settings, `/v2/polls` includes polls that have a persisted validation row and pass all error-severity validation checks. Warning rows are included. To exclude warning rows without changing endpoint code, set:

```toml
[public_dataset]
include_warnings = false
```

To exclude rows that fail a specific validation check, add the check column name:

```toml
[public_dataset]
exclude_failed_checks = ["qc_scope_result_jump"]
```

## Raw Polls

Raw polls are source rows before cleaning and normalization. They are useful for audits, debugging, and tracing a cleaned poll back to its origin.

They are not the recommended endpoint for normal research analysis.

### `GET /v2/raw-polls`

Lists raw scraper/import rows:

```text
GET /v2/raw-polls?limit=100
GET /v2/raw-polls?source=api
GET /v2/raw-polls?worker=forsa
```

Raw poll responses keep source values intact but expose English field names such as `survey_period_raw`, `party_results_raw`, `commissioner_raw`, `election_raw`, and `survey_method_raw`.

### `GET /v2/raw-polls/{raw_poll_id}`

Returns one raw poll row:

```text
GET /v2/raw-polls/R00014382
```

## Reference Data

Reference endpoints expose the canonical keys and labels used in poll data.

Use these endpoints to understand values such as `party_key`, `institute_key`, `survey_method_key`, and `scope`.

```text
GET /v2/parties
GET /v2/institutes
GET /v2/providers
GET /v2/survey-methods
GET /v2/scopes
GET /v2/commissioners
GET /v2/reference-data
```

### Parties

```text
GET /v2/parties
```

Returns canonical party keys, names, short names, and colors where available.

Example keys:

```text
CDU_CSU
SPD
GRUENE
FDP
AFD
LINKE
BSW
SONSTIGE
```

### Institutes

```text
GET /v2/institutes
```

Returns canonical polling institute keys and names.

Example keys:

```text
INSA
FORSA
VERIAN
FORSCHUNGSGRUPPE_WAHLEN
INFRATEST
```

### Survey Methods

```text
GET /v2/survey-methods
```

Returns canonical survey method keys.

Example keys:

```text
ONLINE
TELEFONISCH
TELEFON_ONLINE
PERSOENLICH
UNBEKANNT
```

### Scopes

```text
GET /v2/scopes
```

Returns canonical scope codes and their election keys.

Examples:

```text
federal
nrw
by
be
ost
west
```

### Reference Data Bundle

```text
GET /v2/reference-data
```

Returns the main reference tables in one response. This is useful for clients that want to cache lookup data at startup.

## Elections

### `GET /v2/elections`

Lists election scopes with poll counts and the **next election date** for that
scope (`date`, `year`, `date_is_estimated`). Landtag dates come from
[wahlrecht.de](https://www.wahlrecht.de/umfragen/landtage/). The Bundestag date
is configured via `FEDERAL_ELECTION_DATE` (default `2029-02-25`, estimated).
`latest_publish_date` is the newest poll in that scope, not election day.

```text
GET /v2/elections
```

### `GET /v2/elections/{election_key}`

Returns one election summary:

```text
GET /v2/elections/BUND
GET /v2/elections/NW
```

## Validation Reports

Validation reports describe data quality checks. They are separate from the poll data itself.

### `GET /v2/validation-reports/summary`

Returns aggregate validation quality information:

```text
GET /v2/validation-reports/summary
GET /v2/validation-reports/summary?top=10
```

### `GET /v2/validation-reports`

Lists persisted per-poll validation reports:

```text
GET /v2/validation-reports?limit=100
GET /v2/validation-reports?valid=true
GET /v2/validation-reports?valid=false
```

These persisted validation results drive the quality-controlled default dataset exposed by `/v2/polls` and `/v2/poll-results`.

## Downloads

Downloads expose prebuilt files for bulk use.

### `GET /v2/downloads`

Lists available downloadable assets:

```text
GET /v2/downloads
```

### `GET /v2/downloads/{filename}`

Downloads one exported file.

Examples:

```text
GET /v2/downloads/polls.json
GET /v2/downloads/polls.csv
GET /v2/downloads/polls.parquet
GET /v2/downloads/poll-results.csv
GET /v2/downloads/raw-polls.parquet
GET /v2/downloads/database.sqlite
GET /v2/downloads/metadata.json
```

## Archives

Archive endpoints expose snapshot metadata and download links for archived database exports.

These endpoints require archive storage to be configured.

```text
GET /v2/archives
GET /v2/archives/latest
GET /v2/archives/{filename}
GET /v2/archives/{filename}/download
```

## Health And Metadata

### `GET /`

Returns basic API metadata, including the current API base and legacy API base.

### `GET /health`

Returns service health, data freshness, database status, and validation quality status.

### `GET /heartbeat`

Alias for `/health`.

## Legacy v1 API

The old `/v1` endpoints still work for compatibility:

```text
GET /v1/polls
GET /v1/observations
GET /v1/results
GET /v1/raw-polls
GET /v1/reference/all
GET /v1/download
GET /v1/validation/report
```

They are hidden from `/docs` and `/openapi.json` so new users see only the v2 API.

New integrations should use `/v2`.
