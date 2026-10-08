# AI health weekly reports

Android 0.3.38 (versionCode 41) includes an approximately 50-character chat overview with a **View weekly report** action. The action carries a persistent report ID, so opening an old chat opens the matching week. Health has a reports entry with history, automatic-report preferences, manual generation and update/retry. If an update fails, the saved report remains readable with an explicit retry action.

Reports cover the last complete Monday–Sunday week in the account timezone. AI selects sections, narrative, tables and chart placement based on the available evidence. It emits an HTML fragment and overview with fact/chart placeholders; the server supplies exact measured values and deterministic calendar-axis charts, sanitizes markup and adds a responsive document shell. A separate model check reviews factual consistency before saving. Missing days remain gaps, recorded zero stays zero, main-sleep actual asleep time and explicit naps remain separate, and comparisons require sufficient coverage. Sleep and recovery scores are compared only when their calibration, quality and versions permit.

Enable `AURI_WEEKLY_REPORTS_ENABLED=true` to start the durable report worker. The default is false for self hosting. New Android clients register capability; old clients do not receive unsupported report actions. Automatic reports follow the account's proactive-message switch and report switch; delivery respects sleep/quiet and pacing gates. Credits and token logging use the existing billing path. Reading cached reports makes no model call. Explicit updates reuse the report if its evidence has not changed, otherwise update the same ID without another automatic notification.

Authenticated endpoints under `/v1/health/reports`:

- `POST /client` registers `version_code` (40 or greater).
- `GET/PUT /settings` reads or changes `enabled`.
- `GET /` lists saved reports; `GET /{report_id}` reads a report owned by the caller.
- `POST /weekly` requests the last completed week; `POST /{report_id}/refresh` requests an update or retry.

The SQLite store is `data/reports/weekly.db`. Generation has durable leases and bounded retries. Report messages use fixed IDs for chat deduplication. Account deletion clears reports. Default Xiaomi sync fetches only the last 30 days, so initial reports may have partial history.

The Android reader fetches HTML with native authentication, then displays it in an isolated WebView with JavaScript, remote requests, file access and content access disabled. Report HTML has no links, external scripts, embedded frames or forms. CSP further restricts loading.

Monthly reports are outside this release. Automated checks and synthetic-data model/emulator checks do not establish medical validity or receipt on a particular physical device.
