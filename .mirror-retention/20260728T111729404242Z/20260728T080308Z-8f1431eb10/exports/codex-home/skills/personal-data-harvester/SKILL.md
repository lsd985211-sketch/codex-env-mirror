---
name: personal-data-harvester
description: >
  Design and operate consent-based local imports of a user's own reading,
  watching, collecting, or annotation history into a structured local database.
  Use for official data exports, user-provided files, authorized browser-assisted
  collection, deduplication, incremental sync, or personal knowledge-base intake.
  Do not use to bypass platform controls, conceal automation, or collect other
  people's private data.
---

# Personal Data Harvester

Build a local, inspectable ingestion pipeline for data the user is authorized to access. Prefer exports and local files over browser extraction.

## Role Boundaries

- Collect only the user's own data or data they are explicitly authorized to process.
- Prefer official export, API, share-sheet, RSS, or local application files.
- Use an existing authenticated browser only for visible, user-approved navigation when no safer export exists.
- Never defeat captchas, alter browser fingerprints, hide automation, extract credentials, or evade access controls.
- Keep raw inputs and normalized records local unless the user approves another destination.
- Treat cookies, tokens, account identifiers, and private notes as sensitive.

## Intake Plan

For each source, record:

```yaml
platform: source name
ownership: user_owned | explicitly_authorized
source_type: official_export | local_file | official_api | browser_assisted | manual_link
input_location: path or approved account surface
data_types: [books, videos, notes, favorites]
expected_count: optional
incremental_key: stable source id or timestamp
schedule: manual | interval
retention: raw and normalized retention policy
sensitive_fields: []
```

Do not start collection while ownership, source, or destination is ambiguous.

## Source Priority

1. Official account data export.
2. User-provided CSV, JSON, HTML, SQLite, text, or archive files.
3. Official API or RSS with user authorization.
4. Existing local application data that can be read without bypassing protection.
5. Visible browser-assisted collection with explicit user participation.

If none is available, report the limitation rather than inventing an unsupported scraper.

## When to Load References

Read [references/platforms.md](references/platforms.md) only for a source category relevant to the request. Verify drift-prone paths and schemas before use.

## Storage

Use SQLite for normalized records when the task needs repeatable queries. Keep source-specific raw data separate from the normalized table.

Minimum normalized fields:

```text
source, source_item_id, item_type, title, creator, canonical_url,
user_status, user_rating, user_note, source_updated_at, imported_at,
raw_reference
```

Use `(source, source_item_id)` as the primary identity when available. Preserve raw references so normalization can be audited or rebuilt.

## Workflow

1. Inspect only metadata and a small sample of the supplied source.
2. Confirm schema, encoding, record count, and sensitive fields.
3. Produce a dry-run mapping from source fields to normalized fields.
4. Obtain approval before installing dependencies, using a logged-in browser, or creating a recurring task.
5. Import in bounded batches with idempotent upsert behavior.
6. Validate counts, duplicates, missing identifiers, timestamps, and representative records.
7. Record the last successful cursor or timestamp for incremental sync.
8. Expose status and query results through the owning SQLite or workflow surface.

## Scheduling

On this Windows workspace, use the approved scheduler or Windows Task Scheduler owner for recurring work. Prefer interval execution with a persisted last-run timestamp over fragile once-per-day windows. Do not create cron or launchd configuration.

## Failure Handling

- Schema changed: stop, sample the new schema, and update the mapping before writing.
- Authentication expired: request user action; do not extract or store credentials.
- Rate limit or access block: respect the platform response and pause or switch to an official export.
- Partial import: preserve the cursor and make the next run idempotent.
- Database locked: serialize the writer or use a bounded SQLite timeout.
- Missing identifiers: derive a documented stable key or quarantine the record for review.

## Output Contract

Return:

- authorized sources and selected intake method;
- dry-run mapping and expected record count;
- database or artifact path actually created;
- imported, updated, skipped, duplicate, and failed counts;
- incremental cursor and next scheduled action;
- privacy, permission, or data-quality limitations.
