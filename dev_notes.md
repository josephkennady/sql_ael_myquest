# Developer Notes — AEL MyQuest Pipeline

Full log of all pipeline changes, bug fixes, and design decisions made during active development. Intended as a reference for future maintenance and onboarding.

---

## Table of Contents

1. [PLE Allocation Fix — `is_ple=0` Subjects](#1-ple-allocation-fix--is_ple0-subjects)
2. [Pipeline Orchestrator Auto First-Run Detection](#2-pipeline-orchestrator-auto-first-run-detection)
3. [Full Centre Refresh Uses `centre_ids.sql`](#3-full-centre-refresh-uses-centre_idssql)
4. [write_table CREATE TABLE Fix](#4-write_table-create-table-fix)
5. [Cleanup Graceful First-Run Handling](#5-cleanup-graceful-first-run-handling)
6. [Blank User ID Filter](#6-blank-user-id-filter)
7. [debug_centre.py — Per-Centre Diagnostic Tool](#7-debug_centrepy--per-centre-diagnostic-tool)
8. [CPU and RAM Monitoring in run_pipeline.py](#8-cpu-and-ram-monitoring-in-run_pipelinepy)
9. [Database Schema Notes](#9-database-schema-notes)
10. [Known Gotchas](#10-known-gotchas)

---

## 1. PLE Allocation Fix — `is_ple=0` Subjects

**File:** `sql_queries/production_user_one_record_subject_project_combo.sql`

**Problem discovered:** Centre HDRC Nizar (`72ecca4e-80e3-43ca-ac81-77b72ae04c34`) had 1,044 lesson completions in the source DB but showed 0 completions in the analytics dashboard. Investigation via `debug_centre.py` revealed:

- 30 of 31 learners have `users.is_ple = 1` (PLE learners)
- All 11 subjects mapped to the centre have `subjects.is_ple = 0`
- The `subject_ple_career_path` table had no entries linking the centre's career paths to any subject
- The 3 active career paths in use: "I want to work in a Company" (17 users), "I want to start a business" (12), "I want to work as a freelancer" (4)

**Root cause:** The PLE allocation CTE required `s.is_ple IN (1, 2)` — which excluded all `is_ple=0` subjects entirely for PLE learners. It also required `spcp.subject_id IS NOT NULL` (an INNER JOIN condition on `subject_ple_career_path`) for all subjects regardless of their `is_ple` value.

**Fix applied (PLE allocation CTE, ~line 236–240):**

```sql
-- Before
AND s.is_ple IN (1, 2)
AND pcp.id IS NOT NULL
AND spcp.subject_id IS NOT NULL

-- After
AND s.is_ple IN (0, 1)
AND pcp.id IS NOT NULL
AND (s.is_ple = 0 OR spcp.subject_id IS NOT NULL)
```

**Meaning of the new logic:**
- `is_ple=0` subjects: available to all users (both PLE and non-PLE). No career path mapping needed.
- `is_ple=1` subjects: PLE-specific. Still require the `subject_ple_career_path` mapping.
- `is_ple=2` subjects: non-PLE only, handled by the separate non-PLE CTE.

**Non-PLE path (already correct, no change needed):**
```sql
AND s.is_ple IN (0, 2)
AND (u.is_ple IS NULL OR u.is_ple != 1)
```

---

## 2. Pipeline Orchestrator Auto First-Run Detection

**File:** `run_pipeline.py`

**Problem:** On a new server or when targeting a new table, `run_pipeline.py` always passed `--incremental-users` to Step 1. On first run, the target table did not exist, so incremental mode fell back to a 1970-epoch cutoff and attempted to process ~983,000 user IDs instead of iterating by centre.

**Fix:** Added `_target_table_exists()` at the top of `main()` to check whether the analytics table exists before choosing which mode to run:

```python
def _target_table_exists(table_name: str) -> bool:
    try:
        fetch(ANALYTICS_DB, f"SELECT 1 FROM `{table_name}` LIMIT 0")
        return True
    except pymysql.err.ProgrammingError as exc:
        if exc.args[0] == 1146:
            return False
        raise
```

Decision logic in `main()`:

```python
table_exists = _target_table_exists(args.target_table)
if table_exists:
    # incremental — pick up recently changed users
    step1_cmd = [..., "--incremental-users"]
else:
    # first run — full centre-based rebuild
    step1_cmd = [..., "--centre-sql-path", "sql_queries/centre_ids.sql"]
```

**Effect:** Deploy to a new server, run `python3 run_pipeline.py` once, and the pipeline self-populates the table across all centres. On every subsequent run it uses the fast incremental path automatically.

---

## 3. Full Centre Refresh Uses `centre_ids.sql`

**File:** `run_pipeline.py`

**Problem:** When the auto first-run detection (above) triggered the full centre refresh, it called `run_production_users_by_centre.py` without `--centre-sql-path`. The `--limit` argument in that script defaults to `10`, so only 10 centres were processed. The pipeline log showed: `Found 10 centre ids`.

**Root cause:** `run_production_users_by_centre.py` `parse_args()`:
```python
parser.add_argument(
    "--limit",
    type=int,
    default=10,
    ...
)
```
Without an explicit SQL file, it queries all centres but caps at 10.

**Fix:** Added `--centre-sql-path sql_queries/centre_ids.sql` to the full refresh step command:

```python
step1_cmd = [
    python, "run_production_users_by_centre.py",
    "--target-table", args.target_table,
    "--workers", str(args.workers),
    "--centre-sql-path", "sql_queries/centre_ids.sql",
]
```

`centre_ids.sql` is gitignored (contains real production UUIDs) but exists on the server. It returns all active programme centre IDs.

---

## 4. write_table CREATE TABLE Fix

**File:** `db.py`

**Problem:** When `write_table` was called in `replace` mode, it executed:
```sql
DROP TABLE IF EXISTS `table`;
CREATE TABLE IF NOT EXISTS `table` (...);
```
In some MySQL/RDS environments, the `IF NOT EXISTS` guard on the CREATE after a DROP appears to silently no-op, leaving no table. The subsequent INSERT then fails with error 1146 (table doesn't exist).

**Fix:** `replace` mode now forces a bare `CREATE TABLE` (no `IF NOT EXISTS`) after the drop. `append` mode uses `CREATE TABLE IF NOT EXISTS` for safe auto-creation on first write:

```python
def _create_table_sql(table: str, df: pd.DataFrame, if_not_exists: bool = True) -> str:
    col_defs = ", ".join(...)
    qualifier = "IF NOT EXISTS " if if_not_exists else ""
    return f"CREATE TABLE {qualifier}`{table}` ({col_defs})"

# In write_table:
if if_exists == "replace":
    cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    cur.execute(_create_table_sql(table, df, if_not_exists=False))  # forced create
else:
    cur.execute(_create_table_sql(table, df, if_not_exists=True))   # safe auto-create
```

Same fix applied to `write_table_with_conn`.

---

## 5. Cleanup Graceful First-Run Handling

**File:** `run_cleanup_inactive.py`

**Problem:** On first run the target table does not exist yet. Step 3 (cleanup) would crash with `(1146, "Table 'table' doesn't exist")` when trying to DELETE from a table that Step 1 just created (or hadn't created yet if Step 1 failed).

**Fix:** Wrapped `delete_in_chunks` in a try/except for pymysql error 1146:

```python
except pymysql.err.ProgrammingError as exc:
    if exc.args[0] == 1146:
        logging.warning(
            "Table '%s' does not exist yet — skipping cleanup for column '%s'",
            target_table, column,
        )
        return 0
    raise
```

On first run, cleanup logs a warning and returns 0 rows deleted. The pipeline continues normally.

---

## 6. Blank User ID Filter

**File:** `run_production_users_by_centre.py`, `get_ids()` function

**Problem:** The source DB contained at least one user row with a blank string `''` as its ID. `pandas.dropna()` removes `NaN` values but not empty strings. When this blank ID was passed to the SQL template, the params CTE received an empty string and the query returned a `ValueError: Invalid user id: ` crash.

**Fix:** Added an `if v.strip()` guard after `dropna()`:

```python
# Before
return list(dict.fromkeys(ids[id_column].dropna().astype(str).tolist()))

# After
return list(dict.fromkeys(
    v for v in ids[id_column].dropna().astype(str) if v.strip()
))
```

This filters out any blank or whitespace-only IDs before they reach the SQL engine.

---

## 7. debug_centre.py — Per-Centre Diagnostic Tool

**File:** `debug_centre.py` (new file)

**Purpose:** Traces a single centre through each stage of the pipeline's CTE logic and shows counts at every step. Built to diagnose why HDRC Nizar showed 0 completions in the dashboard despite having 1,044 matched completions in the source DB.

**Usage:**
```bash
python3 debug_centre.py --centre-id 72ecca4e-80e3-43ca-ac81-77b72ae04c34
```

**9 stages:**

| Stage | Checks |
|---|---|
| 1 | Centre existence and active status |
| 2 | Active users (`status=1`, `deleted_at IS NULL`) |
| 3 | Subject-to-centre mapping (`centre_subject`) |
| 4 | Eligible lessons (excludes `pdf`, `mp4`, `pdf web` lesson types) |
| 5 | Non-PLE allocation (`s.is_ple IN (0,2)`, non-PLE users) |
| 6 | PLE allocation deep-dive — checks `ple_career_path_user` records, `job_type_id` FK, `subject_ple_career_path` mapping |
| 7 | Staff/facilitator allocation |
| 8 | Completions in allocated lessons |
| 9 | Existing rows in the analytics target table |

**Key schema discoveries documented during building this tool:**

- `lesson_type` is NOT a direct column on `lessons` — it comes from `LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id` then `lt.name AS lesson_type`
- `batch_id` is on `student_details`, not `users` — join `LEFT JOIN student_details sd ON sd.user_id = u.id` and use `sd.batch_id`
- `ple_career_path_user` uses `job_type_id` as the FK to `ple_career_paths.id`, not `career_path_id` or `ple_career_path_id`
- `rows` is a reserved word in MySQL — use `row_count` as a column alias instead

---

## 8. CPU and RAM Monitoring in run_pipeline.py

**File:** `run_pipeline.py`

Added a background thread that logs CPU and RAM usage every N seconds throughout the pipeline run using `psutil`. This helps diagnose whether slowness or crashes are resource-related.

Functions added:
- `log_system_stats(label)` — logs one line with CPU %, RAM used/total, swap used/total
- `start_monitor(interval)` — starts the background thread, returns a stop event
- `_monitor_loop(stop_event, interval)` — the thread target

Resource snapshots are also logged at: startup, after Step 1, after Step 2, and at shutdown.

Controlled via `--monitor-interval` (default 30 seconds).

---

## 9. Database Schema Notes

Notes discovered during debugging — not obvious from the schema at first glance:

### `ple_career_path_user`
- FK to career paths is `job_type_id`, NOT `career_path_id` or `ple_career_path_id`
- Correct join: `JOIN ple_career_paths pcp ON pcp.id = pcpu.job_type_id`

### `lesson_types`
- `lessons` does not have a direct `lesson_type` text column
- Lesson type name comes from: `LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id`, then use `lt.name`
- Lessons excluded from allocation: `lt.name` IN (`'pdf'`, `'mp4'`, `'pdf web'`) — case-insensitive after `LOWER(TRIM(...))`

### `student_details`
- `batch_id` is on `student_details`, not `users`
- Must join: `LEFT JOIN student_details sd ON sd.user_id = u.id`

### `subjects.is_ple` values
| Value | Meaning |
|---|---|
| `0` | General subject — available to both PLE and non-PLE users |
| `1` | PLE-specific subject — only for users with `users.is_ple = 1`, requires `subject_ple_career_path` mapping |
| `2` | Non-PLE-specific subject — only for users with `users.is_ple != 1` |

### Allocation path summary
- **PLE users** (`users.is_ple = 1`): allocated subjects where `s.is_ple IN (0, 1)`, must have a `ple_career_path_user` record, `is_ple=1` subjects additionally require `subject_ple_career_path`
- **Non-PLE users**: allocated subjects where `s.is_ple IN (0, 2)`, no career path required

---

## 10. Known Gotchas

### `--limit` defaults to 10 — not 0
`run_production_users_by_centre.py` defaults `--limit 10`, meaning if you run it without `--centre-sql-path` and without `--limit 0`, only 10 centres are processed. Always pass `--centre-sql-path sql_queries/centre_ids.sql` for production runs, or `--limit 0` if using the default query.

### `centre_ids.sql` is gitignored — must exist on server
The file `sql_queries/centre_ids.sql` contains real production centre UUIDs and is in `.gitignore`. It is not in the repository. It must be created/maintained on the server directly. `run_pipeline.py` expects this file to exist for first-run full refreshes.

### `IF NOT EXISTS` after `DROP TABLE` can silently fail on RDS
MySQL/RDS can treat `CREATE TABLE IF NOT EXISTS` as a no-op when issued immediately after `DROP TABLE IF EXISTS` in some configurations. The `write_table` replace mode now uses a bare `CREATE TABLE` (forced) after the drop to avoid this.

### `dropna()` does not remove empty strings
`pandas.DataFrame.dropna()` only removes `NaN` / `None`. Empty string `''` IDs pass through. Always filter with `if v.strip()` after `dropna()` when building ID lists from database queries.

### Incremental mode + missing table = wrong behaviour
If `--incremental-users` is passed and the target table does not exist, it falls back to a 1970 epoch cutoff and tries to process all users ever created. The `run_pipeline.py` orchestrator guards against this with `_target_table_exists()` — but if running `run_production_users_by_centre.py` directly, check the table exists first or use `--centre-sql-path`.

### `rows` is a MySQL reserved word
Do not use `rows` as a SQL column alias. Use `row_count` or a similar non-reserved name.

### Workers share the SSH tunnel
In SSH tunnel mode, all workers share one tunnel. High worker counts (> 8) can saturate the tunnel and cause connection timeouts. Set `DB_DIRECT=1` in `.env` when the server is in the same VPC as RDS to bypass the tunnel and allow each worker its own independent connection.
