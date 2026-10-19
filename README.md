# Coursewell

![Coursewell](docs/brand/coursewell-social.png)

A local-first academic organizer that downloads Moodle course materials, extracts assessment dates with source evidence, and synchronizes eligible deadlines and regular classes to Google Calendar.

Built with **Python, Playwright, SQLite, SQLAlchemy, and plain JavaScript**. The current application is a personal macOS workflow; parts of its dashboard configuration are institution-specific. It is not a hosted service or an official Moodle integration.

## What works

- Reuse a browser session for Moodle/Microsoft sign-in, with interactive reauthentication when required.
- Download course files and supported pages, including announcements and attachments; preserve originals and verify downloads with SHA-256.
- Keep a persistent history of new and changed material across repeated updates.
- Extract dates locally from supported documents, with bounded OCR and source excerpts. Uncertain or conflicting dates are held for review.
- Preview classes and assessments in a local calendar and sync eligible events to Google Calendar after user setup.
- Audit the saved library through a relational SQLite reporting index: course coverage, verification gaps, and largest files.

The main workflow is **Update Coursewell → review dates and optional event suggestions → open course materials**. Updates are user-triggered; the app must remain running until they finish. No paid AI API is required. There is no active AI chatbot or automatic upload to ChatGPT Projects.

## Optional second review

The source-review pipeline connects university Outlook and an authorized ChatGPT plan. It reviews ambiguous source text, keeps supporting excerpts, and marks interpretations with **✦ AI**. Supported academic dates can synchronize automatically; optional events require **Add**. Failed reads and conflicting evidence remain visible. There is no paid API fallback, and plan usage limits still apply.

The review inbox uses SQLite for source fingerprints, cached review sections, proposals and persistent decisions. See [pipeline behavior, limits and live verification](docs/information-pipeline.md). Outlook browser automation is institution-specific and requires ongoing compatibility checks; it is not a Microsoft Graph integration.

## Try the SQL demo in under a minute

Requires Python 3.11 or newer. This demo uses only the standard library, fictional records, and a temporary database. It needs no account, browser, API key, or installation.

```bash
python3 scripts/audit_library.py --demo
```

Expected coverage: Demo Discrete Mathematics has 2 files: one is verified and one needs verification. Demo Statistics has zero files. The report also ranks files by size. All demo state is removed when the command finishes.

## SQL implementation

The current dashboard reads file manifests and calendar state from disk. The new SQL audit is a **rebuildable reporting index of those manifests**, not a claim that all application state lives in SQL.

- [Schema](sql/library_schema.sql): course/file relationship, composite primary key, foreign key, validation constraints, verification index, and aggregate view.
- [Coverage query](sql/library_report.sql): LEFT JOIN via the view, GROUP BY, SUM/COUNT, and a bound course filter.
- [Largest-file query](sql/largest_files.sql): CTE and ROW_NUMBER partitioned by course.
- [Implementation](src/library_audit.py): parameterized writes and an atomic snapshot replacement.
- [Tests](tests/test_library_audit.py): repeated imports, invalid snapshots, empty courses, foreign keys, and injection-safe filtering.

```bash
python3 scripts/audit_library.py
python3 scripts/audit_library.py --course COURSE_ID
```

These read `data/library/` and write only `data/library-audit.sqlite`. Verification status comes from the downloader manifest; the audit does not independently hash or open the downloaded files.

Earlier CLI workflows also use SQLAlchemy models for courses, assignments, resources, calendar events, summaries, and scrape runs. See [architecture and SQL discussion](docs/architecture.md) for the distinction.

## Run the application

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r dashboard/requirements-extraction.txt
playwright install chromium
cp .env.example .env
```

Set the Moodle URLs for your institution in `.env`. Do not put credentials in source files. For a first course download:

```bash
python scripts/sync_course.py --login --term F2026
```

Complete sign-in in the browser. Later runs can reuse the saved session. The library is saved under `data/library/course-ID/`. The dashboard's existing Mac launcher and calendar setup are documented in [dashboard/README.md](dashboard/README.md).

**Portability limitation:** the dashboard and launcher currently include paths, course mappings, and timetable configuration specific to the original installation. The download CLI and SQL demo are the portable entry points; do not expect the dashboard to run unchanged on another machine.

## Quality checks

```bash
python -m unittest discover -s tests -v
PYTHONPATH=.:dashboard python -m unittest discover -s dashboard -p 'test_*.py' -v
python scripts/audit_library.py --demo
```

GitHub Actions runs these checks on Python 3.11 and 3.12. Tests use temporary fixtures and mocked external services; they do not prove a live Moodle or Google connection. Frontend work additionally requires rendered desktop/mobile inspection under [AGENTS.md](AGENTS.md).

## Repository map

| Path | Responsibility |
| --- | --- |
| `src/` | Moodle collection, library export, legacy ORM models, SQL audit |
| `dashboard/` | Local HTTP server, UI, extraction and calendar services |
| `scripts/` | CLI entry points and demo |
| `sql/` | Reviewable schema and reporting queries |
| `tests/`, `dashboard/test_*.py` | Offline regression suites |
| `docs/` | Architecture, development workflow and design-tool provenance |
| `data/` | Private runtime data; ignored by Git |

## Privacy and boundaries

Passwords and verification codes stay in the browser. Keep course documents, student records, `.env`, cookies, OAuth tokens, calendar feeds, and databases out of Git. The dashboard binds to loopback and is not intended for public hosting. See [SECURITY.md](SECURITY.md).

Extraction is conservative, not comprehensive: scans, ambiguous announcements, changed instructions, or missing sources can require review. The calendar preview represents local extracted data, not a live mirror of every Google event. Authentication and permissions are controlled by Moodle, Microsoft, and Google.

## Next priorities

1. Replace installation-specific paths and course mappings with portable configuration.
2. Improve extraction using anonymized regression fixtures and measured coverage.
3. Add a simple manually selected assessment file export.

These are future work, not features claimed by this release.

## Branding and compatibility

Coursewell was previously named Academic AI Assistant. Existing installations retain the `academic-ai-assistant` folder, saved authentication location, service identifiers, and calendar UIDs so rebranding does not invalidate sign-ins or duplicate events. Open `dashboard/Coursewell.app` on macOS; the original launcher remains compatible.

### Semester rollover
Coursewell defaults to the current Halifax semester: Winter (January–April), Summer (May–August), Fall (September–December). Settings → Semester can override this to another term. Click Update Coursewell to discover the enrolled Moodle courses for that term; the dashboard, export folders, extraction, AI review and calendar eligibility use the same semester. Course names support `2027W`, `W2027`, and `Winter 2027` formats. Unrecognized semester names are not silently assigned to the current term.

Older course folders, source-review records and Google events are retained. Extracted assessment caches are separated by semester. Regular lecture timetables require verified term-specific times and holiday dates; a new term never inherits last term’s schedule. No application rebuild or course-ID edit is needed to fetch a new semester.

The overall progress bar uses stage weights and completed-course/review counts, not elapsed-time predictions. It may pause while a long request is running; it reaches 100% only after fetching, review and calendar sync finish successfully. Partial updates retain their actionable notice.

### Lecture discovery and optional event choices

Update Coursewell reads active-semester outlines/syllabi for explicit lecture days and start/end times. It combines these with UPEI's general-program class boundaries and no-class dates, saving a term-specific schedule under `data/calendar/schedule-<term>.json`. Source hashes are checked before calendar export. Existing explicit user confirmations remain valid only while the supporting file is unchanged. Calendar → Lecture schedule shows coverage and unresolved courses.

Missing times, conflicting slots, unsupported layouts, and summer-session ambiguity are held back; this is not a guarantee of interpreting every outline. Special-program timetables and course-specific cancellations still require appropriate source review. The official academic calendar is refreshed during updates, with a dated local fallback if unavailable.

Calendar suggestions appear one at a time with Add and Ignore. Add advances only after Google confirms the write; Ignore preserves the original email. Duplicate pending interpretations are resolved together, decisions remain in Previous choices, and stale in-flight refreshes cannot overwrite a successful choice.
