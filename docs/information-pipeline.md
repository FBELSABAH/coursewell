# Coursewell source review

Rollback: `backup/pre-seven-updates-2026-10-17` (`d74044d`), pushed before this work.

## Workflow

**Update Coursewell** checks Moodle and Outlook independently, reads changed sources, runs enabled AI second review, then synchronizes supported academic dates. The app must be running. This is a user-triggered update, not continuous inbox monitoring. Optional events appear in Calendar under **Worth adding?** with Add/Skip. AI interpretations display **✦ AI**. Classes, Assessments and Events use separate Google calendars.

## Outlook

The isolated browser profile checks the university mailbox across folders, excluding sent, drafts, deleted and junk results. First scan covers seven days; later scans overlap the previous successful checkpoint by one day. Conversation fingerprints skip unchanged material. Partial scans retain their checkpoint for retry. Opening mail can mark it read; Coursewell never sends or deletes mail.

Attachments download privately and pass through the file reader. Expected attachments must load before completion. Collection is bounded to 250 conversations, 100 list traversals and 30 attachments per message; incomplete coverage is reported. Sign-in expiry and Microsoft interface changes may require reconnection or maintenance. Clearly personal sensitive messages, such as health-centre correspondence or password-reset mail, are excluded from automatic cloud review. This conservative filter is not a universal privacy classifier.

## AI and evidence

Uses the connected ChatGPT plan with no paid API fallback. Relevant text goes to OpenAI. Ordinary extraction remains first. Ambiguous material and announcements receive a second review, with exact source excerpts and a separate verification pass. Deterministic checks require explicit dates, known courses and unchanged evidence. User corrections and structured Moodle dates are protected. Conflicts and unsupported interpretations remain reviewable.

Long text is split into overlapping sections and successful results are cached. Default batches process up to 25 sources, bounded to approximately 100 inference requests; remaining work stays queued. Academic dates passing all checks can apply automatically when enabled. Optional events always require Add. Stable event IDs protect retries against duplicates. Source changes invalidate pending results and flag prior additions.

## Supported reading

PDF, text, HTML, DOCX, PPTX, XLSX, CSV, supported OpenDocument formats, EML, ICS and bounded ZIP contents; OCR for supported scans/images. Unsupported, encrypted, oversized, nested or unreadable content stays flagged. Neither OCR nor AI guarantees interpretation of every file or relative date.

## SQLite

`.runtime/discovery/discovery.sqlite` stores sources, hashes, section caches, review runs, proposals, decisions and Google IDs. Foreign keys link proposals to sources; SQL queries are parameterized. Credentials, email downloads and browser sessions are ignored by Git. Documents are untrusted data and cannot execute tools.

## Verification (October 18, 2026)

Real ChatGPT-plan inference resolved a STAT 2240 midterm announcement. Live Google sync added it and reported 89 unchanged class entries. Outlook sign-in and real PDF/text attachment downloads succeeded. A complete first-week Outlook scan traversed 16 conversations without collection errors. The following date-filtered check returned no new mail. The full unified update refreshed all five Moodle courses and renewed the Moodle calendar feed; a repeated Google sync reported 93 unchanged events. Initial AI backlog review remains bounded and ongoing; remaining sources resume on later updates. Automated tests cover conflicts, user corrections, changed sources, exact evidence, interrupted long-document review, optional approval and sensitive-mail exclusion.

Validation: 134 dashboard tests and 24 core tests passed. JavaScript syntax and Git whitespace checks passed. Real desktop (1440×900) and mobile (390×844) rendering, keyboard focus, light/dark appearance and browser errors were inspected through browser Playwright controls. CLI browser startup remained sandbox-limited; reduced-motion rules were retained but OS-level reduced-motion emulation was not exercised.

## Review usage and completion (2026-10-18)
AI reviewer requests are recorded locally in discovery.sqlite/ai_usage, with model, timestamp, outcome, and provider-reported input/output/cached/reasoning tokens. Missing measurements are NULL, not zero usage. Counts start when tracking was installed; historical usage and plan percentages cannot be reconstructed. Cached and reasoning tokens are subsets of input/output totals. No source text or credentials are stored in this usage table.

The scheduling filter retains Outlook, announcements, assessment instructions, explicit ambiguity, and due/change language. Ordinary lectures with incidental test terminology are excluded from automatic review, with original documents retained. Existing pending sources are filtered at startup and before review.

Fetching, reading, review, and calendar sync have separate progress phases. Review batches remain bounded at 25 sources; completion reports any remaining queue, including after a service restart. Queue cleared means all eligible sources were interpreted, not that all resulting proposals were approved or all documents were readable.


## Assessment reconciliation and workspace settings

The feed reconciles named assessments by course and assessment identity before
AI targeting and again after accepted interpretations. Pending proposals never
override accepted facts or appear as extra deadlines. Review retains every
proposal and its source evidence. Numbered assessments, course boundaries and
recurring class instances are preserved. Conflicting dates become one undated
review entry, not a guessed deadline. Explicit presentation submission dates
are separated from delivery sessions; a group's assigned slot is never inferred.

Calendar opens with the calendar itself; Study opens with sources and a question.
Google sync, ChatGPT authorization, Outlook and review/usage controls live in
Settings. Moving controls does not change permissions or trigger inference.

## Source-backed in-class assessments

Unnumbered quiz/test occurrences use their course and date for calendar identity;
structured Moodle events replace only the matching occurrence, not every test in
that course. Legacy calendar identities migrate using the saved occurrence date.
Named assessments still reconcile conflicting dates into one review item.

Explicit scheduling announcements can establish a date without AI. Change,
cancellation, relative-date and uncertain wording still require review. A dated
class-session listing can corroborate the same assessment in a tentative schedule;
review sessions do not count as confirmations and different dates remain conflicts.

An explicit in-class reference is joined to one verified lecture slot for that
course/date. Missing, changed, holiday-excluded or multiple slots require review.
Google receives a class-window block with supporting timetable evidence and a
note that the exact assessment duration is unspecified. Exact Moodle timestamps
and user corrections take precedence. A failed live Moodle calendar export does
not block these independently verified local class-window assessments; stale
structured export deadlines remain excluded from writes.
