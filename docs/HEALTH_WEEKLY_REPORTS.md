# AI health weekly reports

Android 0.3.41 (versionCode 44) displays a compact summary (about 50 Chinese characters, at most 80) in the assistant chat bubble. **Details** opens that message's full report in a dedicated reader with tables and charts. The toolbar and system Back return to the saved chat position and input draft. Existing report messages use the same summary entry without rewriting history. Health keeps no report shortcut.

Chat reads only report metadata (`GET /{report_id}?include_html=false`); the reader fetches complete HTML on demand. Saved metadata keeps card height stable when returning. Long generation switches to a background notice after 45 seconds; failures show the appropriate retry action. Saved HTML remains readable if an update fails.

When the user asks about their overall health last week or requests a resend, the ordinary chat agent calls `send_health_report`. It resolves the account's last complete natural week (or a specific Monday date), reuses the matching report, or queues generation when missing. Each requested resend attaches it to a new reply, even if already sent automatically. The durable chat reply acknowledges delivery after saving so generation does not later add an automatic duplicate. `read_health_report` remains available for questions about report conclusions. Reusing HTML makes no report-generation call; the ordinary chat model still uses the normal Credits path. The legacy report action remains the compatible transport reference; current clients render it as a summary and details entry.

Reports cover the last complete Monday–Sunday week in the account timezone. AI selects sections, narrative, tables and chart placement based on the available evidence. It emits an HTML fragment and overview with fact/chart placeholders; the server supplies exact measured values and deterministic calendar-axis charts, sanitizes markup and adds a responsive document shell. A separate model check reviews factual consistency before saving. Missing days remain gaps, recorded zero stays zero, main-sleep actual asleep time and explicit naps remain separate, and comparisons require sufficient coverage. Sleep and recovery scores are compared only when their calibration, quality and versions permit.

Enable `AURI_WEEKLY_REPORTS_ENABLED=true` to start the durable report worker. The default is false for self hosting. New Android clients register capability; old clients do not receive unsupported report actions. Automatic reports follow the account's proactive-message switch and report switch; delivery respects sleep/quiet and pacing gates. Credits and token logging use the existing billing path. Reading cached reports makes no model call. Explicit updates reuse the report if its evidence has not changed, otherwise update the same ID without another automatic notification.

Authenticated endpoints under `/v1/health/reports`:

- `POST /client` registers `version_code` (40 or greater).
- `GET/PUT /settings` reads or changes `enabled`.
- `GET /` lists saved reports; `GET /{report_id}` reads a report owned by the caller; `?include_html=false` omits the HTML body for chat previews.
- `POST /weekly` requests the last completed week; `POST /{report_id}/refresh` requests an update or retry.

The SQLite store is `data/reports/weekly.db`. Generation has a 240-second overall deadline, durable leases and bounded retries. Only transient transport/timeouts retry, with a 60-second backoff; missing records, credit errors and rejected evidence/format stop with the appropriate reason. Xiaomi millisecond update timestamps and imported second timestamps are normalized per record before comparison; malformed metadata does not discard usable health measurements. Report messages use fixed IDs for chat deduplication. Account deletion clears reports. Default Xiaomi sync fetches only the last 30 days, so initial reports may have partial history.

The Android reader fetches HTML with native authentication, then displays it in an isolated WebView with JavaScript, remote requests, file access and content access disabled. Report HTML has no links, external scripts, embedded frames or forms. CSP further restricts loading.

Monthly reports are outside this release. Automated checks and synthetic-data model/emulator checks do not establish medical validity or receipt on a particular physical device.
