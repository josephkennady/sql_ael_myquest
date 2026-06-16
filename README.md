# SQL AEL MyQuest Pipeline

MySQL-based AEL/MyQuest reporting pipeline. Runs per-centre SQL to build a one-record-per-user analytics snapshot, refreshes supplementary user attributes, and cleans up inactive records — with automatic first-run detection and a per-centre diagnostic tool.

---

## Repository Layout

```text
.
├── config.py                          ← DB config from .env
├── db.py                              ← MySQL connection, SSH tunnel, fetch, write helpers
├── run_pipeline.py                    ← Full pipeline orchestrator (run this for cron)
├── run_production_users_by_centre.py  ← Centre/user/incremental refresh engine
├── run_user_addon.py                  ← Supplementary user attributes (gender, batch, platform)
├── run_cleanup_inactive.py            ← Remove inactive user/centre rows from analytics
├── debug_centre.py                    ← 9-stage diagnostic tracer for a single centre
├── sql_queries/
│   ├── production_user_one_record_subject_project_combo.sql  ← Main one-record SQL
│   ├── production_user_one_record_subject_project_combo.md   ← CTE walkthrough & ERD notes
│   ├── centre_ids.sql                 ← [gitignored] Full production centre list
│   ├── centre_ids_limit_10.sql        ← Safe 10-centre example for testing
│   ├── user_addon.sql                 ← Supplementary user attributes query
│   ├── inactive_users_and_centres.sql ← Reference SELECT/DELETE for cleanup
│   ├── superset_sql_jinja_file.sql    ← Superset virtual dataset SQL (Jinja2 filters)
│   └── to_push_to_server.md          ← Server deployment checklist
├── superset_css/
│   ├── ael_superset.css               ← Dashboard custom CSS
│   └── CSS_DOCUMENTATION.md           ← CSS section/chart-ID reference
├── DB_Config/                         ← [gitignored] SSH private key files
├── logs/                              ← [gitignored] Timestamped pipeline log files
├── .env                               ← [gitignored] Real credentials
├── .env.example                       ← Placeholder — copy to .env and fill in
└── .gitignore
```

---

## Quick Start

```bash
cp .env.example .env        # fill in DB and SSH credentials
pip install -r requirements.txt
python3 run_pipeline.py --workers 6
```

The orchestrator auto-detects first run vs incremental:

- **First run** (table does not exist) — runs a full centre-based rebuild using `sql_queries/centre_ids.sql`.
- **Subsequent runs** (table exists) — runs an incremental user refresh picking up only recently changed users.

---

## Full Pipeline Orchestrator

`run_pipeline.py` is the single entry point for the daily pipeline. It runs three steps in order, streams all output to a timestamped log file, and emails the result on completion.

### Steps

| Step | Script | What it does |
|---|---|---|
| 1 | `run_production_users_by_centre.py` | Full centre refresh **or** incremental user refresh (auto-detected) |
| 2 | `run_user_addon.py` | Refreshes the `user_addon` supplementary attributes table |
| 3 | `run_cleanup_inactive.py` | Removes rows for users/centres now deleted or inactive in source DB |

### Auto First-Run Detection

Before Step 1, `run_pipeline.py` checks whether the target table exists in the analytics DB:

- **Table missing** → runs full centre refresh: `run_production_users_by_centre.py --centre-sql-path sql_queries/centre_ids.sql`
- **Table present** → runs incremental refresh: `run_production_users_by_centre.py --incremental-users`

This means you can deploy to a new server and run `python3 run_pipeline.py` once — it creates the table and populates it across all centres automatically.

### Run the Pipeline

```bash
# Standard daily run
python3 run_pipeline.py --workers 6

# Preview what cleanup would delete (no writes)
python3 run_pipeline.py --workers 6 --dry-run

# Skip the email report for this run
python3 run_pipeline.py --workers 6 --no-email

# Custom target table (for testing)
python3 run_pipeline.py --target-table production_users_one_record_test --workers 6
```

### Pipeline Options

```text
--target-table        Analytics table name used across all steps. Default: production_users_one_record
--workers             Parallel workers for the refresh step. Default: 4
--dry-run             Preview cleanup deletes without applying them.
--no-email            Skip the email report for this run.
--monitor-interval    Seconds between CPU/RAM log lines. Default: 30
```

### System Monitor

`run_pipeline.py` logs CPU and RAM usage every `--monitor-interval` seconds throughout the run. Lines look like:

```
[SYSTEM] CPU: 42.3%  |  RAM: 1.2 GB / 3.8 GB (32.1% used)  |  Swap: 0.0 B / 0.0 B (0.0% used)
```

Resource snapshots are also logged at startup, after each step, and at shutdown.

### Log Files

Each run creates a timestamped log in `logs/`:

```
logs/pipeline_2026-06-16_02-00-00.log
```

### Email Reports

Add to `.env` to receive an email after every run:

```text
PIPELINE_EMAIL_SMTP_HOST=smtp.gmail.com
PIPELINE_EMAIL_SMTP_PORT=587
PIPELINE_EMAIL_SMTP_USER=analytics@questalliance.net
PIPELINE_EMAIL_SMTP_PASSWORD=your_app_password
PIPELINE_EMAIL_TO=joseph@questalliance.net
```

For Gmail, generate an **App Password** at [myaccount.google.com → Security → App passwords](https://myaccount.google.com/apppasswords).

The email subject shows the overall result:

```
[AEL Pipeline] SUCCESS — 2026-06-16_02-00-00
[AEL Pipeline] FAILED  — 2026-06-16_02-00-00
```

The body shows the last 100 log lines. The full log is attached as a file.

---

## Centre/User Refresh Engine

`run_production_users_by_centre.py` executes the main SQL once per centre or user and writes all results into one target table.

### Which Mode to Use

| Scenario | Mode | Command flag |
|---|---|---|
| First time — build table from all centres | Centre-based rebuild | `--centre-sql-path sql_queries/centre_ids.sql --replace-target` |
| Resume a partial centre rebuild | Centre-based resume | `--centre-sql-path sql_queries/centre_ids.sql --skip-existing` |
| Daily incremental — recently changed users only | Incremental users | `--incremental-users` |
| Refresh a known list of specific users | User-based | `--user-sql-path sql_queries/user_ids.sql --replace-existing-users` |

### Common Commands

Full centre rebuild (create fresh table):

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --replace-target \
  --workers 8
```

Resume partial centre rebuild (skip already-written centres):

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --skip-existing \
  --workers 8
```

Daily incremental (recently changed users only):

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --incremental-users \
  --workers 4
```

### Command Options

```text
--sql-path                    Main SQL template path. Default: sql_queries/production_user_one_record_subject_project_combo.sql
--centre-sql-path             SQL file returning centre IDs in column 1.
--user-sql-path               SQL file returning user IDs in column 1. Mutually exclusive with --centre-sql-path and --incremental-users.
--incremental-users           Auto-detect recently changed users from destination MAX(created_at). Deletes and reinserts affected rows.
--target-table                Destination table name. Default: production_users_one_record
--limit                       Centre count when no SQL file is given. Default: 10. Use 0 for all.
--replace-target              Drop and recreate the target table on the first write.
--skip-existing               Skip centre/user IDs already in the destination table.
--replace-existing-users      Delete existing rows for each user before inserting (--user-sql-path only).
--workers                     Parallel source-query workers. Default: 1.
--retries                     Retries for a failed centre/user query. Default: 1.
--incremental-overlap-minutes Safety overlap minutes for incremental cutoff. Default: 15.
```

---

## Incremental User Refresh

`--incremental-users` picks up recently changed users automatically:

1. Reads `MAX(created_at)` from the destination table and subtracts the overlap window.
2. Finds users created after that cutoff, or with recent learner/facilitator activity.
3. Deletes their old rows from the destination, then inserts refreshed rows.

Do not combine `--incremental-users` with `--skip-existing` — it would cause already-present users with new activity to be skipped entirely, leaving stale data.

---

## Parallel Workers

Workers only read from the source database. Writes to the destination are single-threaded.

Recommended counts:

| Connection type | Recommended workers |
|---|---|
| SSH tunnel through bastion | 4 to 8 |
| Direct VPC access (`DB_DIRECT=1`) | 8 to 16 |

| Run type | Recommended workers |
|---|---|
| Daily incremental | 4 |
| Full centre rebuild | 6 to 8 |
| Catch-up after multi-day gap | 8 |

Set `DB_DIRECT=1` in `.env` if the server has direct network access to the RDS endpoint (same VPC). Each worker then uses an independent MySQL connection rather than sharing an SSH tunnel.

---

## Cleanup Inactive Records

`run_cleanup_inactive.py` removes rows from the analytics snapshot where the user or centre has since been deleted or set inactive in the source DB.

Run standalone:

```bash
python3 run_cleanup_inactive.py
python3 run_cleanup_inactive.py --dry-run
python3 run_cleanup_inactive.py --target-table production_users_one_record
```

If the target table does not yet exist (first run), cleanup skips gracefully and logs a warning — it does not crash.

```text
--target-table    Analytics table to clean up. Default: production_users_one_record
--dry-run         Preview deletes without applying them.
```

---

## User Addon Pipeline

`run_user_addon.py` reads `sql_queries/user_addon.sql` and writes supplementary user attributes to `quest_analytics.user_addon`.

| Column | Description |
|---|---|
| `user_id` | Source user UUID |
| `username` | Display name |
| `gender` | Normalised: `Male`, `Female`, or `Other` |
| `centre_name` / `org_name` | Centre and organisation |
| `state_name` / `district_name` | Location |
| `trade` | Enrolled trade |
| `batch_name` / `batch_status` | Batch from `student_details`; `1` if linked to active non-deleted batch |
| `centre_type` / `ple_enabled` | Centre metadata |
| `platform` | Registration platform (`created_platform`) |
| `first_login` | Earliest login from `login_logs` |

```bash
python3 run_user_addon.py              # full replace (default)
python3 run_user_addon.py --append     # append to existing table
```

---

## Per-Centre Diagnostic Tool

`debug_centre.py` traces a single centre through all 9 pipeline processing stages and shows exactly where data is being dropped.

```bash
python3 debug_centre.py --centre-id 72ecca4e-80e3-43ca-ac81-77b72ae04c34
```

### Stages Checked

| Stage | What it checks |
|---|---|
| 1 | Centre exists and is active |
| 2 | Active users (status=1, not deleted) |
| 3 | Subject-to-centre mapping (`centre_subject`) |
| 4 | Eligible lessons (filtered by lesson type — excludes pdf, mp4, pdf web) |
| 5 | Non-PLE allocation (subjects with `is_ple IN (0,2)`, users without `is_ple=1`) |
| 6 | PLE allocation deep-dive (subjects with `is_ple IN (0,1)`, career path via `job_type_id`, `subject_ple_career_path` mapping) |
| 7 | Staff/facilitator allocation |
| 8 | Completion counts in allocated lessons |
| 9 | Existing rows in the analytics target table |

Use this tool when a centre shows zero completions in the dashboard but has learners with activity in LMS. The stage output pinpoints exactly where the allocation chain breaks.

---

## PLE Allocation Rules

The main SQL (`production_user_one_record_subject_project_combo.sql`) allocates lessons to users via two paths:

### Non-PLE Path
- User: `is_ple IS NULL OR is_ple != 1`
- Subject: `is_ple IN (0, 2)`
- No career path required

### PLE Path
- User: `is_ple = 1`
- Subject: `is_ple IN (0, 1)` — subjects with `is_ple=0` are included (they apply to all users)
- Career path: user must have a `ple_career_path_user` record; join via `job_type_id` (FK to `ple_career_paths.id`)
- For `is_ple=1` subjects only: `subject_ple_career_path` mapping must exist between subject and career path
- For `is_ple=0` subjects: no `subject_ple_career_path` mapping required

Key rule: `(s.is_ple = 0 OR spcp.subject_id IS NOT NULL)` — `is_ple=0` subjects bypass the career path mapping check.

---

## Scheduled Runs (Cron)

```bash
crontab -e
```

Add (runs at 2:00 AM daily):

```
0 2 * * * cd /path/to/pipeline && python3 run_pipeline.py --workers 6 >> logs/cron.log 2>&1
```

`run_pipeline.py` saves a full timestamped log to `logs/` and emails it. The `>> logs/cron.log` captures startup errors before the logger initialises.

Verify:

```bash
crontab -l
tail -f logs/cron.log
ls -lt logs/pipeline_*.log | head -5
```

---

## Environment Setup

```bash
cp .env.example .env
```

Source DB (via SSH tunnel or direct):

```text
SOURCE_SSH_HOST=
SOURCE_SSH_PORT=
SOURCE_SSH_USER=
SOURCE_SSH_PKEY_FILE=
SOURCE_RDS_HOST=
SOURCE_RDS_PORT=
SOURCE_DB_USER=
SOURCE_DB_PASSWORD=
SOURCE_DB_NAME=
```

Analytics (destination) DB:

```text
DEST_SSH_HOST=
DEST_SSH_PORT=
DEST_SSH_USER=
DEST_SSH_PKEY_FILE=
DEST_RDS_HOST=
DEST_RDS_PORT=
DEST_DB_USER=
DEST_DB_PASSWORD=
DEST_DB_NAME=
```

Optional — skip SSH tunnel if server is inside the same VPC as RDS:

```text
DB_DIRECT=1
```

Private key files go in `DB_Config/` (gitignored).

---

## Sensitive Information Policy

Gitignored files that must never be committed:

| Pattern | Reason |
|---|---|
| `.env` | Real credentials |
| `*.pem` | SSH private keys |
| `DB_Config/` | Key files directory |
| `sql_queries/centre_ids.sql` | Contains real production centre UUIDs |
| `sql_queries/user_ids.sql` | Contains real user UUIDs |
| `sql_queries/user_ids_incremental.sql` | Same |
| `logs/` | Pipeline logs (may contain IDs) |
| `output/` | Exported data |

Before pushing:

```bash
git status --short
git diff --cached
```

---

## Validation

Syntax-check all Python files:

```bash
python3 -m py_compile db.py config.py run_production_users_by_centre.py \
  run_user_addon.py run_cleanup_inactive.py run_pipeline.py debug_centre.py
```

---

## Troubleshooting

### Centre shows zero completions in dashboard

Run the diagnostic tool:

```bash
python3 debug_centre.py --centre-id <uuid>
```

Check each stage in order. Common causes:
- No subjects mapped to the centre (`centre_subject` table)
- All lessons are `pdf`/`mp4`/`pdf web` type (filtered out)
- PLE users with no matching `ple_career_path_user` record
- `subject_ple_career_path` mapping missing for `is_ple=1` subjects

### `TypeError: not enough arguments for format string`

Happens when SQL contains literal `%` characters (e.g. `LIKE '%ASSESSMENT%'`) and PyMySQL tries to substitute them. `db.fetch()` calls `execute(sql)` without parameters when none are provided, avoiding this.

### Empty result for a centre

- The centre exists and is active — check Stage 1 of `debug_centre.py`
- Users exist for that centre — check Stage 2
- Subject/lesson/allocation mapping — Stages 3–6

### Duplicate rows

Rerunning without `--replace-target` appends. Use `--replace-target` for a clean rebuild, or `--skip-existing` when resuming a partial run.

### MySQL temporary table errors (1146 on source DB)

Large parallel reads can hit internal MySQL temp table limits. The runner retries once by default. If errors persist, reduce `--workers`:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --workers 2 --skip-existing --retries 2
```

### Cleanup fails on first run (table does not exist)

`run_cleanup_inactive.py` catches MySQL error 1146 (table not found) and logs a warning instead of crashing. Safe to ignore on first run.

---

## Superset Virtual Dataset

`sql_queries/superset_sql_jinja_file.sql` powers the Youth QApp Phoenix AEL dashboard. It joins `production_users_one_record` with `user_addon` and applies Jinja2 `filter_values()` expressions for each dashboard filter widget.

See `sql_queries/production_user_one_record_subject_project_combo.md` for the full CTE walkthrough and ERD.

---

## Superset CSS

`superset_css/ael_superset.css` — custom CSS for the AEL dashboard.

Apply: Edit dashboard → CSS editor → paste → Save.

See `superset_css/CSS_DOCUMENTATION.md` for the full section breakdown, chart ID reference, and extension guide.

### Brand Colours

| Token | Hex | Usage |
|---|---|---|
| Brand blue | `#156fb5` | Headers, borders, KPI text |
| Brand orange | `#f7941d` | Assessment accents, gradient end |
