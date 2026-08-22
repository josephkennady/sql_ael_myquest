# AEL Pipeline Runbook

Every way to run this pipeline, with copy-paste examples and the reasoning behind each one.

For architecture, setup and SQL logic see [README.md](README.md). This document is about
**operating** the pipeline: which command to run in which situation, and what it will do.

---

## Table of Contents

- [The 30-second version](#the-30-second-version)
- [Mental model](#mental-model)
- [Pick your scenario](#pick-your-scenario)
- [Scenario 1 — Routine daily refresh](#scenario-1--routine-daily-refresh)
- [Scenario 2 — Catching up after a gap](#scenario-2--catching-up-after-a-gap)
- [Scenario 3 — Full rebuild from scratch](#scenario-3--full-rebuild-from-scratch)
- [Scenario 4 — First-time setup on a new server](#scenario-4--first-time-setup-on-a-new-server)
- [Scenario 5 — Backfilling missing centres](#scenario-5--backfilling-missing-centres)
- [Scenario 6 — Refreshing a single centre](#scenario-6--refreshing-a-single-centre)
- [Scenario 7 — Refreshing a specific list of users](#scenario-7--refreshing-a-specific-list-of-users)
- [Scenario 8 — Rebuilding only the downstream tables](#scenario-8--rebuilding-only-the-downstream-tables)
- [Scenario 9 — Testing safely against a scratch table](#scenario-9--testing-safely-against-a-scratch-table)
- [Scenario 10 — The without-career-path variant](#scenario-10--the-without-career-path-variant)
- [Scenario 11 — Recovering from a failed or partial run](#scenario-11--recovering-from-a-failed-or-partial-run)
- [Scenario 12 — Fixing duplicate rows](#scenario-12--fixing-duplicate-rows)
- [Scenario 13 — Centre-wise refresh with retry sweeps](#scenario-13--centre-wise-refresh-with-retry-sweeps)
- [Scheduling with cron](#scheduling-with-cron)
- [Reading the logs](#reading-the-logs)
- [Command reference](#command-reference)
- [Traps and gotchas](#traps-and-gotchas)

---

## The 30-second version

```bash
./run_incremental_refresh.sh          # routine run — use this 95% of the time
./run_incremental_refresh.sh --since-days 5   # catching up after a gap
./run_full_refresh.sh                 # rebuild everything from scratch
```

Both scripts run all four pipeline steps. Everything below is for the cases these two
commands do not cover.

---

## Mental model

### The four steps

| Step | Script | Target table | Write mode |
|---|---|---|---|
| 1 | `run_production_users_by_centre.py` | `production_users_one_record` | Incremental or full rebuild |
| 2 | `run_user_addon.py` | `user_addon` | Always `DROP` + rebuild |
| 3 | `run_cleanup_inactive.py` | `production_users_one_record` | `DELETE` inactive users/centres |
| 4 | `run_sql_filters.py` | `sql_ael_filters` | Always `DROP` + rebuild |

Only step 1 has modes. Steps 2-4 always do the same thing, and they are cheap relative to
step 1 — which is why every scenario below ends by running them.

### Two ways to slice step 1

Step 1 runs the main SQL repeatedly, scoped either **per centre** or **per user**:

- **Centre mode** — one query per centre, scoped by `u.centre_id = '<uuid>'`. Far fewer
  queries, so it is much faster for bulk work. Cannot reach users whose `centre_id` is NULL.
- **User mode** (`--incremental-users`) — one query per user. Slower per user, but precisely
  targeted and it covers every user regardless of centre.

Rule of thumb: **bulk work → centre mode. Change-driven work → user mode.**

### How writes actually land

Tables are created with **no primary key and no unique index**, so nothing in the database
will stop a duplicate row. What protects you is the flag you pass:

| Flags | Write behaviour | Safe to re-run? |
|---|---|---|
| *(none)* | Plain `INSERT` — appends | **No — duplicates everything** |
| `--replace-target` | `DROP` + `CREATE` on first non-empty result, then appends | Yes, but table is empty mid-run |
| `--skip-existing` | Appends, skipping IDs already present | Yes — but never updates existing rows |
| `--replace-existing-users` | `DELETE` that ID, then insert, one transaction | Yes |
| `--incremental-users` | As above, per user (implies `--replace-existing-users`) | Yes |

Writes are atomic per ID: all of a centre's rows go in on one connection with a single
commit, so a centre is either fully present or fully absent — never half-loaded.

### Where the incremental cutoff comes from

`--incremental-users` builds its user list from two independent parts:

1. **Cutoff-based** — users whose `created_at` **or** `updated_at` is at or after the cutoff,
   across `users`, `student_details`, `learning_activities` and
   `facilitator_learning_activities`. The cutoff defaults to `MAX(created_at)` in the
   destination table minus 5 minutes.
2. **Gap check** — every active user in source that has no row in the destination at all.

Part 2 is the safety net: it self-heals missing users no matter what the cutoff says, which
is why an ordinary incremental run also backfills entirely missing centres.

> **The derived cutoff is not "the last run time".** The snapshot's `created_at` column holds
> `users.created_at` — the newest *registration* in the table. It is always at or before the
> last run, so nothing gets missed, but it moves with registration activity rather than with
> your schedule. Use `--since-days N` when you want a predictable fixed window.

---

## Pick your scenario

```
Is the table missing or being built for the first time?
  └── YES → Scenario 4 (first-time setup)

Did the SQL logic or source schema change?
  └── YES → Scenario 3 (full rebuild)

Is this a normal scheduled/manual update?
  └── YES → Scenario 1 (routine)

Has it not run for several days?
  └── YES → Scenario 2 (catch-up)

Are whole centres missing from the table?
  └── YES → Scenario 5 (backfill) — or just Scenario 1, the gap check covers it

Is one centre wrong or stale?
  └── YES → Scenario 6 (single centre)

Do you have a specific list of users to fix?
  └── YES → Scenario 7 (user list)

Is only the Superset filter table stale?
  └── YES → Scenario 8 (downstream only)

Did a run fail partway?
  └── YES → Scenario 11 (recovery)

Do you see doubled numbers in the dashboard?
  └── YES → Scenario 12 (duplicates)
```

---

## Scenario 1 — Routine daily refresh

**When:** normal operation, scheduled or manual.

```bash
./run_incremental_refresh.sh
```

**What happens:** step 1 refreshes users changed since the derived cutoff plus any user
missing from the destination, deleting and reinserting each user's rows. Steps 2-4 rebuild
`user_addon`, delete inactive rows, and rebuild `sql_ael_filters`. A timestamped log lands in
`logs/pipeline_*.log` and is emailed.

**Options:**

```bash
WORKERS=8 ./run_incremental_refresh.sh      # more parallel source readers
./run_incremental_refresh.sh --no-email     # skip the email report
./run_incremental_refresh.sh --dry-run      # preview the step 3 deletes only
```

**Typical duration:** proportional to the number of changed users — check the
`Total users to refresh` line early in the log.

---

## Scenario 2 — Catching up after a gap

**When:** the pipeline has not run for days; you want a guaranteed lookback window.

```bash
./run_incremental_refresh.sh --since-days 5
```

Or pin an exact date:

```bash
./run_incremental_refresh.sh --since 2026-08-11
```

**Why bother, given the derived cutoff already works?** It does work — the derived cutoff is
always at or before your last run, so it cannot miss anything. `--since-days` buys
*predictability*, not correctness: a fixed window regardless of registration patterns.

**Cost:** a wider window means more users to reprocess. `--since-days 5` on a daily job
reprocesses five days of changes every night. For steady-state scheduling, 1-2 days of
overlap is plenty.

---

## Scenario 3 — Full rebuild from scratch

**When:** the SQL logic changed, the source schema changed, or you suspect widespread
corruption. This is the only operation that fixes rows whose source data changed *without*
`updated_at` being bumped.

```bash
./run_full_refresh.sh          # prompts before dropping the table
./run_full_refresh.sh -y       # unattended
```

**What happens:** step 1 runs in centre mode over every centre with `--replace-target`, so
the table is dropped and recreated from the current SQL. Then steps 2-4. A failed step aborts
the run, so downstream tables are never built on a half-loaded snapshot. Logs to
`logs/full_refresh_*.log`.

**Zero-downtime variant** — build into a staging table, then swap:

```bash
TARGET_TABLE=production_users_one_record_new ./run_full_refresh.sh -y
# then, in the analytics DB:
#   RENAME TABLE production_users_one_record     TO production_users_one_record_old,
#                production_users_one_record_new TO production_users_one_record;
```

**Step 1 only**, without the downstream steps:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --limit 0 \
  --replace-target \
  --workers 8
```

**Two things to know:**

- The table is **empty or partial from the first centre until the run finishes**. Superset
  will show wrong numbers throughout. Use the staging-table variant if that matters.
- The schema is inferred from whichever centre completes first, mapping that DataFrame's
  pandas dtypes to MySQL types. Most columns land as `TEXT`, but a numeric column that is
  clean `int64` for the first centre becomes `BIGINT` — and with parallel workers, which
  centre wins the race varies between runs.

---

## Scenario 4 — First-time setup on a new server

**When:** fresh deployment, target table does not exist.

Complete the setup checklist in [README.md](README.md) first (`.env`, `.pem` keys,
dependencies), then:

```bash
python3 -m py_compile db.py config.py run_pipeline.py   # syntax check
./run_incremental_refresh.sh
```

`run_pipeline.py` detects the missing table and automatically runs a full centre refresh for
the first-time populate, then continues through steps 2-4.

> **Verify `sql_queries/centre_ids.sql` before this run.** The first-run branch drives step 1
> from that file, and it is gitignored scratch — if it holds a single test centre, your
> "first-time populate" loads exactly one centre. It should contain the full list:
> ```sql
> SELECT id FROM centres WHERE status = 1 AND deleted_at IS NULL;
> ```
> Alternatively skip the pipeline for the first load and use `./run_full_refresh.sh`, which
> uses `--limit 0` and ignores the file entirely.

---

## Scenario 5 — Backfilling missing centres

**When:** a few centres are entirely absent from the table.

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --limit 0 \
  --skip-existing \
  --workers 8

python3 run_sql_filters.py \
  --source-table production_users_one_record \
  --target-table sql_ael_filters
```

`--limit 0` enumerates all centres; `--skip-existing` removes every centre that already has
rows, so only the missing ones are queried and inserted. No duplicates, no reprocessing.

**You may not need this at all.** An ordinary `./run_incremental_refresh.sh` already backfills
missing users through the gap check, and it updates `sql_ael_filters` too. Use this scenario
when the missing volume is large enough that centre mode's one-query-per-centre is
meaningfully faster than user mode's one-query-per-user.

**What this will not fix:**

- Centres that are present but **stale** — `--skip-existing` never updates existing rows. Use
  Scenario 6.
- Centres that are missing because they have **no active users** — the SQL returns no rows,
  nothing is written, and they will be re-queried on every future backfill. Harmless, but it
  never "resolves".

---

## Scenario 6 — Refreshing a single centre

**When:** one centre is wrong, stale, or you just re-ran a fix for it.

Put the centre ID in a SQL file:

```bash
echo "SELECT '72ecca4e-80e3-43ca-ac81-77b72ae04c34'" > sql_queries/centre_ids.sql
```

Then:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --replace-existing-users \
  --workers 4
```

`--replace-existing-users` deletes that centre's existing rows and inserts fresh ones in a
single transaction.

> **`--replace-existing-users` is not optional here.** Without it the run is a plain `INSERT`
> and you get a second copy of every row for that centre. See Scenario 12.

The user-mode equivalent, which also catches users whose centre assignment changed:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --incremental-users \
  --centre-id 72ecca4e-80e3-43ca-ac81-77b72ae04c34 \
  --since 2026-01-01 \
  --workers 4
```

Follow either with `run_sql_filters.py` so Superset reflects the change.

To investigate *why* a centre looks wrong before touching it:

```bash
python3 debug_centre.py --centre-id 72ecca4e-80e3-43ca-ac81-77b72ae04c34
```

---

## Scenario 7 — Refreshing a specific list of users

**When:** support has a list of users whose data looks wrong.

```bash
cat > sql_queries/user_ids.sql << 'EOF'
SELECT id FROM users WHERE id IN (
  '00000000-0000-0000-0000-000000000000',
  '11111111-1111-1111-1111-111111111111'
);
EOF

python3 run_production_users_by_centre.py \
  --user-sql-path sql_queries/user_ids.sql \
  --target-table production_users_one_record \
  --replace-existing-users \
  --workers 4
```

Any query returning user IDs in the first column works — including one that derives the list,
e.g. all users in a batch. `sql_queries/user_ids.sql` is gitignored.

---

## Scenario 8 — Rebuilding only the downstream tables

**When:** the snapshot is correct but `sql_ael_filters` or `user_addon` is stale — typically
after running step 1 by hand.

```bash
python3 run_user_addon.py --target-table user_addon
python3 run_cleanup_inactive.py --target-table production_users_one_record
python3 run_sql_filters.py --source-table production_users_one_record --target-table sql_ael_filters
```

Preview the cleanup deletes without applying them:

```bash
python3 run_cleanup_inactive.py --target-table production_users_one_record --dry-run
```

> Any time you run step 1 on its own, `sql_ael_filters` is left stale — Superset keeps serving
> the old filter values until you run step 4.

---

## Scenario 9 — Testing safely against a scratch table

**When:** validating a SQL change without touching production.

```bash
TARGET_TABLE=production_users_one_record_test ./run_incremental_refresh.sh --no-email
```

Or a full build into a test table:

```bash
TARGET_TABLE=production_users_one_record_test ./run_full_refresh.sh -y
```

Then point the filter table at it:

```bash
python3 run_sql_filters.py \
  --source-table production_users_one_record_test \
  --target-table sql_ael_filters_test
```

> If the test table does not exist yet, `run_incremental_refresh.sh` triggers the first-run
> **full centre refresh** driven by `centre_ids.sql` — which can be a very long run. Check
> that file first, or use `run_full_refresh.sh` which does not depend on it.

---

## Scenario 10 — The without-career-path variant

`production_users_one_record_without_career_path` includes PLE users who have no career path
assigned. It is a separate table with its own SQL, and `run_pipeline.py` never touches it —
the orchestrator has no `--sql-path` flag, so it always uses the main SQL.

**Full rebuild:**

```bash
python3 run_production_users_by_centre.py \
  --sql-path sql_queries/production_user_one_record_without_career_path.sql \
  --target-table production_users_one_record_without_career_path \
  --limit 0 \
  --replace-target \
  --workers 6
```

**Incremental refresh:**

```bash
python3 run_production_users_by_centre.py \
  --sql-path sql_queries/production_user_one_record_without_career_path.sql \
  --target-table production_users_one_record_without_career_path \
  --incremental-users \
  --workers 6
```

Both wrapper scripts can drive it via environment overrides:

```bash
TARGET_TABLE=production_users_one_record_without_career_path \
SQL_PATH=sql_queries/production_user_one_record_without_career_path.sql \
./run_full_refresh.sh -y
```

(`SQL_PATH` is honoured by `run_full_refresh.sh` only — `run_incremental_refresh.sh` goes
through `run_pipeline.py`, which has no `--sql-path` passthrough.)

---

## Scenario 11 — Recovering from a failed or partial run

**First, find out what actually failed.**

```bash
ls -lt logs/*.log | head -5
grep -E "STEP FAILED|FAIL |failed and were skipped" logs/pipeline_<timestamp>.log
```

**If individual centres or users failed** — they are listed after
`ids failed and were skipped`. Each exhausted its retries and was skipped; **the run still
exited 0**, so a green summary does not mean complete data. Re-run:

```bash
# Missing centres
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record --limit 0 --skip-existing --workers 4

# Or let the gap check handle it
./run_incremental_refresh.sh
```

**If a full refresh died partway** — the table holds only the centres that completed. Either
re-run `./run_full_refresh.sh -y` from scratch, or resume with `--skip-existing` (faster,
since completed centres are atomic and trustworthy):

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record --limit 0 --skip-existing --workers 8
```

**If the source DB was the problem** (MySQL temp-table errors, connection resets under
parallel load) — lower the worker count and raise retries:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --limit 0 --skip-existing --workers 2 --retries 3
```

**Always finish with steps 2-4** (Scenario 8) so downstream tables match the snapshot.

---

## Scenario 12 — Fixing duplicate rows

**Symptom:** dashboard counts are exactly doubled for some centres or users.

**Cause:** step 1 was run without `--replace-target`, `--replace-existing-users` or
`--skip-existing`, so it appended on top of existing rows. There is no unique index to stop it.

**Check:**

```sql
SELECT user_id, COUNT(*) AS n
FROM production_users_one_record
GROUP BY user_id
HAVING n > 1
LIMIT 20;
```

**Fix for one affected centre** — re-run it with the delete-first flag, which clears every
existing row for that centre before reinserting:

```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --replace-existing-users \
  --workers 4
```

**Fix if duplication is widespread:** `./run_full_refresh.sh` — a clean rebuild.

Then rebuild the filter table (Scenario 8), since `sql_ael_filters` inherited the duplicates.

---

## Scenario 13 — Centre-wise refresh with retry sweeps

**When:** the per-user incremental run is too slow, or the source DB keeps rejecting queries
and you want failures retried automatically instead of by hand.

This is the mode to schedule if `run_incremental_refresh.sh` can no longer finish overnight.

### Why centre mode is so much faster

Both modes run the same SQL; they differ only in what they scope it to. The expensive part of
that SQL — `centre_subject` mapping, lesson eligibility filtering, PLE/non-PLE allocation
expansion — is **per-centre work**. In user mode you pay it once per user; in centre mode you
pay it once and every user in the centre rides along.

A real comparison from this pipeline: a 12-day catch-up produced **114,938 changed users**
spanning **575 centres**. User mode was averaging ~18 seconds per user and had taken three
days to reach 74%. The same window in centre mode is 575 queries.

The trade is query *weight*: a centre with 1,361 users materialises that whole allocation
expansion in one MySQL internal temp table. That is what makes concurrency dangerous here —
see the `--workers` note below.

### Running it

```bash
./run_centre_refresh_cron.sh                     # last 5 days, the default
SINCE_DAYS=12 ./run_centre_refresh_cron.sh       # wider catch-up window
WORKERS=1 SWEEPS=4 ./run_centre_refresh_cron.sh  # source DB under pressure
NO_EMAIL=1 ./run_centre_refresh_cron.sh          # no email report
```

What it does, in order:

1. **Regenerates `sql_queries/changed_centres.sql`** for the window. The cutoff is computed in
   Python (`now - SINCE_DAYS`), so no shell date arithmetic and no `%` escaping in the crontab.
   The query mirrors the pipeline's own changed-user logic — `created_at` **or** `updated_at`
   across `users`, `student_details`, `learning_activities`, `facilitator_learning_activities`
   — then maps those users to their centres.
2. **Sweep 1** — refreshes every centre in that list with `--replace-existing-users`, so each
   centre is deleted and reinserted in one transaction.
3. **Sweeps 2..N** — re-runs only the centres that failed, using the `centre_retry_*.sql` the
   runner wrote, with `COOLDOWN` seconds between each.
4. **Steps 2-4** — `user_addon`, cleanup inactive, `sql_ael_filters`.
5. **Emails** the log, unless `NO_EMAIL=1`.

### The two retry layers

Source-side failures here are usually transient temp-space exhaustion, which clears once
other queries finish. So retries are deliberately spread out rather than hammered:

| Layer | Control | Default | Spacing | Retries what |
|---|---|---|---|---|
| Inner | `RETRIES` | 2 → 3 attempts | ~2s, inside the runner | one centre's query |
| Outer | `SWEEPS` | 3 | `COOLDOWN`, default 600s | every centre still failing |

**Total attempts per centre = `SWEEPS × (RETRIES + 1)` = 9 with the defaults.** Only the
centres that are still failing enter each later sweep, so sweeps 2 and 3 are typically tiny.

To spread attempts across a whole night rather than half an hour:

```bash
COOLDOWN=3600 SWEEPS=4 ./run_centre_refresh_cron.sh   # 4 sweeps an hour apart
```

### Environment overrides

| Variable | Default | Meaning |
|---|---|---|
| `SINCE_DAYS` | 5 | Days of change to look back over |
| `SWEEPS` | 3 | Outer passes over failed centres |
| `RETRIES` | 2 | Inner retries, so attempts per sweep = `RETRIES + 1` |
| `COOLDOWN` | 600 | Seconds between sweeps |
| `WORKERS` | 2 | Parallel source queries |
| `TARGET_TABLE` | `production_users_one_record` | Analytics table |
| `SQL_PATH` | main one-record SQL | Snapshot SQL template |
| `CENTRE_SQL` | `sql_queries/changed_centres.sql` | Where the generated list is written |
| `PYTHON` | `python3` | Interpreter — set this in cron |
| `NO_EMAIL` | unset | `1` skips the email report |

### Why `WORKERS` defaults to 2, not 6

Each centre query materialises that centre's entire allocation expansion in a MySQL internal
temporary table on the RDS instance. Six of those at once exhausts the instance's temp space,
and queries start dying with a misleading error:

```
(1146, "Table './rdsdbdata/tmp/#sql3e9_19c24_1f' doesn't exist")
```

Error 1146 normally means "table doesn't exist" — here the "table" is MySQL's own temp file,
which vanished because the temp space filled. **It is a source-DB capacity problem, not a bug
in your data or SQL.** Lowering concurrency is the fix; raising it makes the failure rate
worse. Start at 2 and only go up if a full sweep completes cleanly.

### Exit codes and failure handling

| Outcome | Downstream steps | Exit code |
|---|---|---|
| All centres refreshed | Run | 0 |
| Some centres still failing after every sweep | **Run** — the snapshot is mostly fresh, filters should reflect it | 1 |
| Sweep 1 exits non-zero (crash, bad args, killed) | **Skipped** — aborts rather than rebuilding filters off an unrefreshed snapshot | the runner's code |

If centres remain, the script prints the leftover retry file and the user-mode fallback.

### A centre that fails every sweep

A centre large enough to exhaust temp space on its own will not be rescued by lower
concurrency. Switch that one to user mode, where each query handles a single user and the
temp tables stay small:

```bash
python3 run_production_users_by_centre.py \
  --target-table production_users_one_record \
  --incremental-users \
  --centre-id <failing-centre-uuid> \
  --since 1970-01-01 \
  --workers 4
```

`--since 1970-01-01` makes every user in that centre eligible, giving full centre coverage
processed one user at a time. Slower, but it completes.

### Two caveats inherited from centre mode

**Users with `centre_id IS NULL` are unreachable.** The SQL filters on
`u.centre_id = p.centre_id` and no centre ID matches NULL. Check whether you have any:

```sql
SELECT COUNT(*) FROM users WHERE centre_id IS NULL AND status = 1 AND deleted_at IS NULL;
```

If non-zero, run a periodic user-mode pass (Scenario 1) alongside this one.

**A user who moved between centres can end up duplicated.** Refreshing their new centre
deletes rows `WHERE centre_id = <new>`, but their old rows sit under the old centre_id and
survive until that centre is also refreshed. Check after a run:

```sql
SELECT user_id, COUNT(DISTINCT centre_id) c
FROM production_users_one_record
GROUP BY user_id HAVING c > 1 LIMIT 20;
```

Centre mode is strictly *more* complete in every other respect — it refreshes all users in a
touched centre, including ones whose source data changed without `updated_at` being bumped,
which user mode can never detect.

---

## Scheduling with cron

```bash
crontab -e
```

Daily at 2:00 AM:

```
0 2 * * * PYTHON=/usr/bin/python3 /path/to/pipeline/run_incremental_refresh.sh >> /path/to/pipeline/logs/cron.log 2>&1
```

With a fixed 2-day lookback:

```
0 2 * * * PYTHON=/usr/bin/python3 /path/to/pipeline/run_incremental_refresh.sh --since-days 2 >> /path/to/pipeline/logs/cron.log 2>&1
```

Centre-wise instead, with automatic retry sweeps (Scenario 13) — use this when the per-user
run can no longer finish overnight:

```
0 2 * * * PYTHON=/usr/bin/python3 SINCE_DAYS=5 /path/to/pipeline/run_centre_refresh_cron.sh >> /path/to/pipeline/logs/cron.log 2>&1
```

Pick **one** of these as your scheduled job — running both on the same table wastes work.

**Three cron-specific traps:**

1. **`PYTHON=/usr/bin/python3`** — cron's `PATH` is minimal and often lacks `python3`. Both
   wrapper scripts honour this variable, and both `cd` to their own directory so `.env` and
   relative SQL paths resolve.
2. **Never put `date` output in a crontab.** Cron treats `%` as a newline separator, so
   `date +%Y-%m-%d` must be escaped as `date +\%Y-\%m-\%d`. `--since-days N` does the
   arithmetic in Python and sidesteps this entirely — as well as the `date -d` (GNU) versus
   `date -v` (BSD/macOS) split.
3. **Absolute paths for the redirect.** `>> logs/cron.log` resolves against cron's working
   directory, usually `$HOME`.

Do not schedule `run_full_refresh.sh`. It is a deliberate, supervised operation.

Verify:

```bash
crontab -l
tail -f /path/to/pipeline/logs/cron.log
ls -lt /path/to/pipeline/logs/pipeline_*.log | head -5
```

---

## Reading the logs

| Log line | Meaning |
|---|---|
| `Incremental user refresh cutoff: <ts>` | The cutoff actually used — check it matches your intent |
| `Cutoff-based changed users: N` | Users caught by the timestamp comparison |
| `Missing from destination: N` | Users caught by the gap check |
| `Total users to refresh: N` | The real workload for this run |
| `Found N centre ids` | Centre-mode workload — **if this is 1, you hit the `centre_ids.sql` trap** |
| `Skipping N existing centre ids` | `--skip-existing` filtered these out |
| `Replaced centre <id> after deleting N existing rows` | Delete-then-insert worked |
| `Skipping invalid user id (not a UUID)` | Corrupt source data, skipped rather than crashing |
| `N centre ids failed and were skipped` | **Data is incomplete despite exit code 0** |
| `All requested centre ids already exist. Nothing to write.` | The run was a no-op |
| `PIPELINE SUMMARY` | Per-step PASS/FAIL — the authoritative result |

### Failed/succeeded ID files

Every run of `run_production_users_by_centre.py` writes, as it goes:

| File | Contents |
|---|---|
| `logs/<type>_ok_<ts>.txt` | IDs that completed, one per line |
| `logs/<type>_failed_<ts>.txt` | `<id><TAB><error>` for each failure |
| `logs/<type>_retry_<ts>.sql` | Ready to pass back as `--centre-sql-path`/`--user-sql-path` |

Both text files are flushed after every line, so a killed process or a dropped SSH session
still leaves a complete record. The retry `.sql` is written at the end and only when there
were failures — its absence means the run was clean. The runner also prints the exact retry
command on exit.

**A green email is not proof of a complete run.** `run_pipeline.py` continues through steps
2-4 even when step 1 fails, and step 1 exits 0 even when individual centres or users were
skipped. Check `PIPELINE SUMMARY` and grep for `failed and were skipped`.

---

## Command reference

### Wrapper scripts

| Command | Effect |
|---|---|
| `./run_incremental_refresh.sh` | All 4 steps, incremental step 1 |
| `./run_incremental_refresh.sh --since-days N` | Same, with a fixed N-day cutoff |
| `./run_incremental_refresh.sh --since YYYY-MM-DD` | Same, with a fixed date cutoff |
| `./run_incremental_refresh.sh --dry-run` | Preview step 3's deletes |
| `./run_incremental_refresh.sh --no-email` | Skip the email report |
| `./run_full_refresh.sh` | All 4 steps, full rebuild, with confirmation prompt |
| `./run_full_refresh.sh -y` | Same, unattended |
| `./run_centre_refresh_cron.sh` | Centre-wise refresh + retry sweeps + steps 2-4 (Scenario 13) |
| `SINCE_DAYS=12 ./run_centre_refresh_cron.sh` | Same, wider window |

Environment overrides: `WORKERS`, `TARGET_TABLE`, `PYTHON` (all scripts);
`SQL_PATH`, `ADDON_TABLE`, `FILTER_TABLE` (full refresh); `SINCE_DAYS`, `SWEEPS`, `RETRIES`,
`COOLDOWN`, `CENTRE_SQL`, `NO_EMAIL` (centre refresh).

### Step 1 flag combinations

| Goal | Flags |
|---|---|
| Rebuild everything | `--limit 0 --replace-target` |
| Backfill missing centres | `--limit 0 --skip-existing` |
| Refresh listed centres in place | `--centre-sql-path <file> --replace-existing-users` |
| Refresh listed users in place | `--user-sql-path <file> --replace-existing-users` |
| Refresh changed users | `--incremental-users` |
| Refresh changed users, one centre | `--incremental-users --centre-id <uuid>` |
| Refresh changed users since a date | `--incremental-users --since YYYY-MM-DD` |

Rejected combinations (argparse will stop you): `--replace-target` with `--skip-existing`;
`--incremental-users` with either; `--since` or `--centre-id` without `--incremental-users`.

### Worker counts

| Connection | Run type | Workers |
|---|---|---|
| SSH tunnel (`DB_DIRECT=0`) | Incremental | 4-6 |
| SSH tunnel (`DB_DIRECT=0`) | Full rebuild | 6-8 |
| Direct VPC (`DB_DIRECT=1`) | Incremental | 8 |
| Direct VPC (`DB_DIRECT=1`) | Full rebuild | 8-16 |

Workers only parallelise **reads**. Writes are serialised in the main thread, so raising the
count past the point where the source DB saturates buys nothing. With `DB_DIRECT=0` all
workers multiplex over one SSH transport per database, which is materially slower than direct
VPC access.

---

## Traps and gotchas

**`sql_queries/centre_ids.sql` is gitignored scratch.** It is meant to hold the full centre
list, but it is routinely overwritten with a single ID for testing — and nothing warns you.
Any command using `--centre-sql-path` inherits whatever is in it, including
`run_pipeline.py`'s first-run branch. Prefer `--limit 0`, which uses the built-in
`SELECT c.id FROM centres c`. If you must use the file, check it first:

```bash
cat sql_queries/centre_ids.sql
```

**No unique index exists on any table.** Nothing in the database prevents duplicate rows. The
flag you pass is the only safeguard. See the write-behaviour table in
[Mental model](#mental-model).

**A green pipeline email does not mean complete data.** Steps 2-4 run even when step 1 fails,
and step 1 exits 0 even when centres or users were skipped.

**Centre mode cannot reach users with `centre_id IS NULL`.** The SQL filters on
`u.centre_id = p.centre_id`, and no centre ID matches NULL. Only user mode covers them.

**Incremental refresh cannot detect changes that did not touch `updated_at`.** A direct SQL
update on the source that left the timestamp alone is invisible to both the derived cutoff and
`--since`. Only a full rebuild picks it up.

**Running step 1 alone leaves `sql_ael_filters` stale.** Superset keeps serving the old filter
values until step 4 runs.

**`--skip-existing` works at ID granularity, not row granularity.** One existing row for a
centre marks the whole centre as done. This is reliable because writes are atomic per centre,
but it means the flag never repairs stale data — only absence.

**`--replace-target` drops the table at the start of the run.** Everything reading it sees
empty or partial data until the run finishes, and a mid-run failure leaves it that way.

**A learner's phase can arrive without a batch.** `phase_users` assigns a phase
directly, with no `student_details.batch_id`. Both that route and the batch route
feed `main_phases`; if phase counts look low, check whether the affected users have
a batch at all before suspecting the refresh. Direct assignment applies to
`user_type IN (3, 4)` only, so a phase count will not include facilitators or admins.
See "Phase Attribution" in [README.md](README.md).

**Error 1146 on the source DB is not a missing table.** A message naming
`./rdsdbdata/tmp/#sql...` means MySQL's own internal temp table vanished because the RDS
instance ran out of temp space. It is a concurrency/capacity problem — lower `--workers`,
do not raise it. Centre mode provokes this far more than user mode because each query is
much larger.

**`--workers N` does nothing when there is only one ID to process.** A single-centre run is a
single task no matter the worker count.
