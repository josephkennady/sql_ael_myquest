# SQL AEL MyQuest Pipeline

This repository contains a MySQL-based AEL/MyQuest reporting pipeline. The current runner executes the one-record-per-user production SQL once for each centre ID or user ID and appends all outputs into one destination table.

The pipeline is designed for cases where the centre list is selected separately, for example:

```sql
SELECT c.id
FROM centres c
LIMIT 10;
```

The IDs are read by Python, injected one by one into the `params` CTE of the main SQL, and written into the same target table.

## Repository Layout

```text
.
├── config.py
├── db.py
├── run_production_users_by_centre.py
├── run_user_addon.py
├── sql_queries/
│   ├── production_user_one_record_subject_project_combo.sql
│   ├── centre_ids_limit_10.sql
│   ├── user_addon.sql
│   └── superset_sql_jinja_file.sql
├── superset_css/
│   ├── ael_superset.css
│   └── CSS_DOCUMENTATION.md
├── .env.example
└── .gitignore
```

Key files:

- `run_production_users_by_centre.py`: Python entry point for centre-by-centre or user-by-user execution.
- `run_user_addon.py`: Python entry point to run `user_addon.sql` and write results to the analytics DB.
- `sql_queries/production_user_one_record_subject_project_combo.sql`: Main MySQL 8 SQL query that returns one row per user.
- `sql_queries/production_user_one_record_subject_project_combo.md`: Detailed SQL notes, CTE flow, ERD, allocation rules, and maintenance guide.
- `sql_queries/centre_ids_limit_10.sql`: Safe example centre-list query.
- `sql_queries/user_addon.sql`: Supplementary user-level attributes query (gender, batch status, platform, first login). Writes to `quest_analytics.user_addon`.
- `sql_queries/superset_sql_jinja_file.sql`: Superset virtual dataset SQL with Jinja2 filter expressions for the Youth QApp Phoenix AEL dashboard.
- `superset_css/ael_superset.css`: Custom CSS for the Youth QApp Phoenix AEL Superset dashboard.
- `superset_css/CSS_DOCUMENTATION.md`: Full documentation for the dashboard CSS — section breakdown, chart ID reference, flip card mechanics, and extension guide.
- `db.py`: MySQL connection, SSH tunnel, fetch, and write helpers.
- `config.py`: Environment-driven source and destination DB configuration.
- `.env.example`: Placeholder environment configuration. Copy this to `.env` locally and fill in real values.

## What The Runner Does

For each centre ID:

1. Reads the main SQL template.
2. Replaces values inside the `params` CTE:
   - `user_id = NULL`
   - `centre_id = current centre ID`
   - `batch_id = NULL`
3. Runs the SQL against the source database.
4. Appends the returned rows into one destination table.

For each user ID, the runner instead injects:

- `user_id = current user ID`
- `centre_id = NULL`
- `batch_id = NULL`

The target table is not split by centre or user. All rows are written into the same table, with `centre_id` and `user_id` available as columns in the output.

## Requirements

- Python 3.10 or newer
- MySQL 8 or newer on the source database
- Python packages:

```bash
pip install -r requirements.txt
```

The source SQL uses CTEs and window functions such as `ROW_NUMBER()`, so MySQL 8+ is required.

## Environment Setup

Copy the example file:

```bash
cp .env.example .env
```

Fill in `.env` with your local database and SSH tunnel values.

Required source DB variables:

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

Required destination DB variables:

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

Private key files should be stored in `DB_Config/`.

Do not commit `.env`, private keys, or `DB_Config/`.

## Centre List SQL

Use a separate SQL file to control which centres are processed.

A safe example is included:

```bash
sql_queries/centre_ids_limit_10.sql
```

Example content:

```sql
SELECT c.id
FROM centres c
LIMIT 10;
```

For local work, you can create your own:

```bash
sql_queries/centre_ids.sql
```

That file is ignored by Git because it may contain real programme IDs, project IDs, centre filters, or other operational details.

## User List SQL

You can also process specific users from a local SQL file:

```bash
sql_queries/user_ids.sql
```

Example content:

```sql
SELECT u.id
FROM users u
WHERE u.id IN (
  '00000000-0000-0000-0000-000000000000',
  '11111111-1111-1111-1111-111111111111'
);
```

That file is ignored by Git for the same reason as `centre_ids.sql`.

For incremental refreshes, use `--incremental-users`. The runner reads the cutoff from the destination DB first, then queries changed users from the source DB. This is needed because the source and destination schemas are on different database connections.

## Run The Pipeline

### Which Command Should I Run?

Use one run mode at a time:

| Scenario | Command mode | Duplicate protection |
|---|---|---|
| Build table from scratch for a centre list | `--centre-sql-path ... --replace-target` | Recreates target table |
| Resume centre processing into an existing table | `--centre-sql-path ... --skip-existing` | Skips centres already in target |
| Refresh a known list of users | `--user-sql-path ... --replace-existing-users` | Deletes and reinserts those users |
| Automatically refresh recently changed users | `--incremental-users` | Deletes and reinserts changed users |

`--incremental-users` cannot be combined with `--centre-sql-path`. Centre refresh handles centre allocation changes. Incremental user refresh handles newly registered users and recent learning activity.

Recommended daily incremental user refresh:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --workers 1 \
  --incremental-users
```

Recommended centre resume run:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --workers 2 \
  --skip-existing
```

Run with the example centre list and recreate the destination table on the first non-empty result:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids_limit_10.sql \
  --target-table production_users_one_record \
  --replace-target
```

Run with your local centre-list SQL:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --replace-target
```

Run multiple centres in parallel:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --replace-target \
  --workers 4
```

Append to the existing destination table without recreating it:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record
```

Recommended server resume command. This does not replace the table; it skips centres already present in the destination table:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --workers 2 \
  --skip-existing
```

Run the default first 10 centres without a separate centre-list file:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --replace-target
```

Run all centres:

```bash
python3 run_production_users_by_centre.py \
  --limit 0 \
  --target-table production_users_one_record \
  --replace-target
```

Run with your local user-list SQL and replace the whole target table:

```bash
python3 run_production_users_by_centre.py \
  --user-sql-path sql_queries/user_ids.sql \
  --target-table production_users_one_record \
  --replace-target
```

Run an incremental user refresh without duplicates:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --workers 1 \
  --incremental-users
```

## Command Options

```text
--sql-path
    Path to the main SQL file.
    Default: sql_queries/production_user_one_record_subject_project_combo.sql

--centre-sql-path
    Optional SQL file that returns centre IDs in the first column.

--user-sql-path
    Optional SQL file that returns user IDs in the first column.
    Cannot be used together with --centre-sql-path or --incremental-users.

--incremental-users
    Build the user ID list automatically.
    Reads MAX(created_at) from the destination table, subtracts the configured overlap,
    then queries changed users from the source users and activity tables.
    Automatically deletes and replaces existing rows for changed users.
    Cannot be used with --replace-target or --skip-existing.

--target-table
    Destination table name.
    Default: production_users_one_record

--limit
    Number of centres to process when no ID mode is provided.
    Default: 10
    Use 0 to process all centres.

--replace-target
    Recreate the target table on the first non-empty result.
    Without this flag, results are appended to the existing table.

--workers
    Number of parallel source-query workers.
    Default: 1
    Start with 2 or 4. Higher values can overload the source DB or SSH tunnel.

--skip-existing
    Skip IDs already present in the destination table.
    In centre mode, checks centre_id.
    In user mode, checks user_id.
    Cannot be used with --replace-target.
    Do not use this for incremental learning-activity refreshes because existing users with new activity must be refreshed.

--retries
    Number of retries for a failed centre/user source query.
    Default: 1
    A failed ID is skipped after all retry attempts are exhausted.

--replace-existing-users
    Only valid with --user-sql-path.
    Deletes existing destination rows for each user before inserting the refreshed result.
    Use this for manual user-list refreshes to avoid duplicate user rows.
    Cannot be used with --replace-target or --skip-existing.

--incremental-overlap-minutes
    Safety overlap in minutes for --incremental-users cutoff.
    Default: 5
```

## Rerun Behaviour

Use `--replace-target` when you want a fresh rebuild:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --replace-target
```

Without `--replace-target`, the script appends rows. This is useful when extending a table, but it can create duplicates if you rerun the same centre list.

Use `--skip-existing` with append mode when resuming a partial run:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --workers 2 \
  --skip-existing
```

With this option, the runner checks the destination table before processing:

- centre mode checks `centre_id`
- user mode checks `user_id`
- IDs already present are skipped
- IDs not present are processed and appended

## Incremental User Refresh

Use incremental user refresh when you only want to update users who recently registered or recently created learning activity.

Do not combine this with `--centre-sql-path`. If centre allocation changed, run a centre refresh separately with a centre list and `--skip-existing` or `--replace-target` depending on whether you are resuming or rebuilding.

The runner does this in two separate database calls:

1. Destination DB: reads `MAX(created_at)` from the target table and subtracts a 5 minute safety overlap.
2. Source DB: finds users created after that cutoff, or users with learner/facilitator activity created after that cutoff.

Then run:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --workers 1 \
  --incremental-users
```

Use a larger overlap if needed:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --workers 1 \
  --incremental-users \
  --incremental-overlap-minutes 10
```

Why incremental mode deletes and replaces existing users:

- some users already exist in `production_users_one_record`
- those users may now have new learning activity
- `--skip-existing` would skip them and miss updates
- plain append would create duplicate `user_id` rows
- incremental mode deletes each affected user's old row first, then inserts the refreshed row

## Parallel Runs

Use `--workers` to run multiple centre or user queries in parallel. Workers only read from the source database. Writes to the destination table are still handled by the main process so `--replace-target` is applied once and all rows go into the same target table.

Recommended starting values:

- `--workers 1`: safest, original sequential behavior
- `--workers 2`: conservative parallel run
- `--workers 4`: faster for larger centre lists if the DB and SSH tunnel can handle it

Avoid high worker counts unless the source database and bastion have enough capacity.

## Sensitive Information Policy

The repository is configured to avoid committing local secrets:

- `.env` is ignored.
- `*.pem` is ignored.
- `DB_Config/` is ignored.
- `sql_queries/centre_ids.sql`, `sql_queries/user_ids.sql`, and `sql_queries/user_ids_incremental.sql` are ignored because local ID-list queries may include real IDs.
- Generated outputs under `output/` are ignored.

Before pushing to GitHub, check:

```bash
git status --short
git diff --cached
```

Do not stage files containing:

- real passwords
- private keys
- database hostnames
- SSH usernames
- production-only IDs that should not be public
- exported data

## Validation

Syntax-check the Python files:

```bash
python3 -m py_compile db.py run_production_users_by_centre.py
```

The pipeline itself requires live database access, so a full end-to-end run should only be done from an environment with valid SSH and DB credentials.

## Troubleshooting

### `TypeError: not enough arguments for format string`

This can happen when PyMySQL receives SQL containing literal `%` characters, such as:

```sql
LIKE '%ASSESSMENT%'
```

The current `db.fetch()` implementation avoids that by calling `execute(sql)` when no query parameters are provided.

### Empty result for a centre

The runner skips writes for centres that return no rows.

Check:

- the centre exists and is active
- users exist for that centre
- the SQL filters match the required user types
- related project, batch, subject, and lesson mappings exist

### Duplicate rows

If you rerun without `--replace-target`, rows are appended. Use `--replace-target` for a clean rebuild.

### Checking write progress

During a parallel run, each centre that produces data should log:

```text
Running centre 12/1835: <centre-id> (active users=57)
Writing N rows for centre ...
Wrote N rows for centre ...
Progress [####--------------------------] centre 12/1835 (0.65%), users 842/128000 (0.66%), current centre users=57
```

Centres with no matching output log:

```text
Centre <id> returned no rows. Skipping write.
Progress [####--------------------------] centre 13/1835 (0.71%), users 901/128000 (0.70%), current centre users=59
```

The progress bar is based on active user count, not only centre count. This is useful because some centres have many more users than others.

For resume runs, use `--skip-existing` and watch the destination count in another SQL window:

```sql
SELECT COUNT(DISTINCT centre_id)
FROM quest_analytics.production_users_one_record;
```

### MySQL temporary table errors

During large parallel reads, MySQL can sometimes fail a centre query with an internal temporary table error similar to:

```text
pymysql.err.ProgrammingError: (1146, "Table './rdsdbdata/tmp/#sql...' doesn't exist")
```

The runner retries failed centre/user source queries once by default. If the same ID still fails, it logs the failed ID, marks progress for that ID, skips it, and continues with the next centre/user.

Use a lower worker count if this happens repeatedly:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --workers 1 \
  --skip-existing
```

You can also increase retries:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --workers 2 \
  --skip-existing \
  --retries 2
```

---

## User Addon Pipeline

`run_user_addon.py` reads `sql_queries/user_addon.sql` against the source database and writes the result to `quest_analytics.user_addon` in the analytics database.

The table provides supplementary user-level attributes that are joined into the Superset virtual dataset:

| Column | Description |
|---|---|
| `user_id` | Source user UUID |
| `username` | Display name |
| `gender` | Normalised to `Male`, `Female`, or `Other` |
| `centre_name` | Centre the user belongs to |
| `org_name` | Organisation |
| `state_name` / `district_name` | Location |
| `trade` | Enrolled trade |
| `batch_name` | Batch name from `student_details` |
| `batch_status` | `1` if user is linked to an active, non-deleted batch; `0` otherwise |
| `centre_type` | Centre type |
| `ple_enabled` | Whether the centre has PLE enabled |
| `platform` | Registration platform (`created_platform`) |
| `first_login` | Earliest login timestamp from `login_logs` |

### Run User Addon

Full replace (default):

```bash
python3 run_user_addon.py
```

Append to existing table:

```bash
python3 run_user_addon.py --append
```

Custom SQL path or target table:

```bash
python3 run_user_addon.py \
  --sql-path sql_queries/user_addon.sql \
  --target-table user_addon
```

### User Addon Command Options

```text
--sql-path
    Path to the SQL file.
    Default: sql_queries/user_addon.sql

--target-table
    Destination table name in the analytics DB.
    Default: user_addon

--append
    Append rows instead of replacing the table.
    Without this flag the table is replaced on each run.
```

---

## Superset Virtual Dataset SQL

`sql_queries/superset_sql_jinja_file.sql` is the SQL behind the Superset virtual dataset that powers the Youth QApp Phoenix AEL dashboard.

It joins `quest_analytics.production_users_one_record` (one row per user, produced by the main pipeline) with `quest_analytics.user_addon` (supplementary attributes).

### Jinja2 Filters

All dashboard filters are wired through Superset's `filter_values()` Jinja2 function. The query declares one `{% set %}` variable per filterable column and appends the corresponding `AND` clause only when the filter is active.

**Plain column filters** (string `IN` list):

```sql
{% set state_name_filter = filter_values('state_name') | select('string') | list %}
{% if state_name_filter %}
  AND state_name IN ({{ "'" + "', '".join(state_name_filter) + "'" }})
{% endif %}
```

**JSON array filters** (`project_combos`, `subject_combos`) use `JSON_SEARCH` to match any element in the array:

```sql
{% if prog_name_filter %}
  AND JSON_VALID(project_combos) = 1
  AND (
    {% for val in prog_name_filter %}
      JSON_SEARCH(project_combos, 'one', '{{ val }}', NULL, '$[*].prog_name') IS NOT NULL
      {% if not loop.last %} OR {% endif %}
    {% endfor %}
  )
{% endif %}
```

**Numeric filter** (`rounded_completion`) uses `| map('int')` to cast values before the `IN` clause:

```sql
{% set rounded_completion_filter = filter_values('rounded_completion') | map('int') | list %}
{% if rounded_completion_filter %}
  AND ROUND(a.completion_pct) IN ({{ rounded_completion_filter | join(', ') }})
{% endif %}
```

### Adding a New Filter

1. Add a `{% set %}` line using `filter_values('column_name')`.
2. Add the corresponding `{% if %}` / `AND` clause.
3. For JSON columns use the `JSON_SEARCH` pattern above.
4. Create a matching filter widget in the Superset dashboard and point it at the column name.

---

## Superset CSS Dashboard Styling

`superset_css/ael_superset.css` contains all custom CSS for the Youth QApp Phoenix AEL Superset dashboard.

See `superset_css/CSS_DOCUMENTATION.md` for a full breakdown of every section, the chart ID reference table, and the guide for extending the CSS.

### How to Apply

1. Open the dashboard in Superset.
2. Click **Edit dashboard** (top right).
3. Open the **CSS** editor (three-dot menu or dedicated CSS tab depending on your Superset version).
4. Paste the full contents of `ael_superset.css`.
5. Click **Save**.

### Key Features

- **KPI glassmorphism cards** — frosted white front face (`backdrop-filter: blur(16px)`) with brand accent borders per group.
- **Flip card effect** — hovering any KPI card squishes the front face away and springs in a dark macOS-style frosted glass back face (`backdrop-filter: blur(28px)`) showing the metric description.
- **Brand gradients** — KPI numbers use a blue-to-orange gradient text (`#156fb5` → `#f7941d`).
- **Section headers** — full blue gradient background with white bold text; small sub-headers rendered as uppercase pills.
- **Dark mode compatible** — backgrounds that should flip in dark mode omit `!important` so Superset's built-in dark theme can override them.

### Brand Colours

| Token | Hex | Usage |
|---|---|---|
| Brand blue | `#156fb5` | Primary colour — headers, borders, KPI text, info icons |
| Brand orange | `#f7941d` | Secondary colour — assessment accents, gradient end, table headers |
