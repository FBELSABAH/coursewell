# Local dashboard

The installed macOS application opens `http://127.0.0.1:8767`. Its app bundle must
stay alongside the supporting dashboard files. A Finder alias may live elsewhere.
Keep the app running while downloads and calendar sync finish.

## Everyday workflow

1. Click **Update Moodle**. A valid saved session is reused. If it expires,
   complete Microsoft sign-in in the browser; never enter codes in source files.
2. Review upcoming assessments and recent changes. Repeated updates preserve
   change history even if the latest run finds no new files.
3. Use **My courses** or **File library** to open saved originals.
4. Open **Calendar** for the local month/agenda preview, course filters, source
   evidence, excluded dates, and Google connection controls.

The sidebar collapses to a rail and supports keyboard navigation and reduced
motion. Planner chat and paid AI interpretation are not active features.

## Google Calendar

Google OAuth setup is required before connecting. The app manages its own
calendar and retains local event identities to avoid duplicate writes. Sync runs
after a user-triggered Moodle update when enabled, or via the calendar sync action.
The preview displays local eligible events; it is not a live mirror of Google.

Regular classes come from the private verified timetable. Source changes,
uncertain dates, and failures can hold entries for review. Do not assume every
Moodle announcement becomes a calendar event. Existing remote edits and deletions
are handled conservatively. Disconnect removes local credentials without deleting
existing Google events. OAuth session expiry may require reconnection.

## Extraction and announcements

Local rules read supported Moodle pages, announcements, and document formats.
PDF extraction and bounded OCR require `requirements-extraction.txt`; OCR also
requires the Tesseract executable. Source excerpts and coverage reports expose
uncertain, unreadable, or skipped material. This is not exhaustive understanding.

Announcements are collected from identified course announcement/news forums;
reply, edit, submission, and quiz-attempt actions are not automated. Changed or
missing posts do not imply that an assessment has been cancelled.

## Installation boundaries

This dashboard was built around one student's Mac installation. `server.py`,
launcher and sync modules contain installation-specific paths and course mappings.
Review these before using another machine. The root README provides portable
CLI/demo entry points. Do not publish a copy of the real private data to make a demo.

The persistent Moodle browser profile lives under
`~/Library/Application Support/Academic Assistant/auth`. `.runtime/` stores local
dashboard state. Calendar credentials and course downloads remain in ignored
private storage. The HTTP service binds to loopback; do not expose it publicly.

## Development

`dashboard/` in the Git repository is canonical. An existing installed launcher
may serve a deployed mirror. Verify which copy is running before expecting source
changes to appear. Never replace private runtime state when deploying source files.

From the repository root:

```bash
PYTHONPATH=.:dashboard python -m unittest discover -s dashboard -p 'test_*.py' -v
```

Read `AGENTS.md` for rendered frontend verification requirements. Offline tests do
not confirm live Microsoft/Google authentication or successful remote sync.


### Separate Google calendars and assessment colors

Sync keeps regular classes in **Coursewell · Classes** and deadlines in
**Coursewell · Assessments**. Both appear independently in Google Calendar's
calendar list, where either can be hidden. Exams/tests are red, quizzes yellow,
assignments blue, other assessments purple, and classes gray. Categories use
explicit title keywords; an unrecognized assessment remains a general assessment.
The local preview includes the same categories and a labeled color legend.

The previous university calendar is reused for Classes. Existing managed
assessment records, including past dates, migrate with deterministic event IDs:
copy to Assessments, verify, then remove the unedited original using its ETag.
Progress is persisted for safe retries after interruptions. Manually edited,
deleted, or enriched Google events are preserved and reported for review.
No broader Google OAuth scope is required. After an expired authorization,
reconnect Google and click **Sync calendar now** to apply the split.
