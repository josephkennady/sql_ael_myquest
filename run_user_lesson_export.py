"""
run_user_lesson_export.py — lesson-level detail for a list of users, exported to CSV.

Grain: one row per user-lesson. Each row is one allocated lesson for a user, with
its completion flag, score, rating and duration — the "melted" view, no JSON to
parse. Nothing is written to the analytics DB; this is a read-only export.

It runs sql_queries/production_user_lesson_progress.sql once per user, using the
same params-CTE injection the pipeline uses, then concatenates every result into a
single CSV. By default the CSV is enriched with the readable columns from
quest_analytics.user_addon (name, centre, state, district, batch, trade) so the
file is usable without looking UUIDs up by hand.

Usage:
    # From a SQL file that returns user IDs in the first column
    python3 run_user_lesson_export.py --user-sql-path sql_queries/user_ids.sql

    # Ad-hoc, a few IDs on the command line
    python3 run_user_lesson_export.py --user-id 00000000-0000-0000-0000-000000000000

    # Faster, and a named output file
    python3 run_user_lesson_export.py \
        --user-sql-path sql_queries/user_ids.sql \
        --out output/s2sd_vti_pilot_2025_2026.csv \
        --workers 6

    # Include pdf / mp4 / pdf web, which the SQL drops by default
    python3 run_user_lesson_export.py \
        --user-sql-path sql_queries/user_ids.sql \
        --all-lesson-types

    # Or choose your own exclusions
    python3 run_user_lesson_export.py \
        --user-sql-path sql_queries/user_ids.sql \
        --exclude-lesson-types 'mp4'

    # Every user of one centre, in a single query
    python3 run_user_lesson_export.py \
        --centre-id 00000000-0000-0000-0000-000000000000 \
        --all-lesson-types \
        --out output/centre_lessons.csv

    # Same, but one query per user (for a centre too large for a single query)
    python3 run_user_lesson_export.py \
        --centre-id 00000000-0000-0000-0000-000000000000 --per-user --workers 4

    # Subject-level instead of lesson-level
    python3 run_user_lesson_export.py \
        --user-sql-path sql_queries/user_ids.sql \
        --sql-path sql_queries/production_user_subject_progress.sql
"""

import argparse
import datetime
import logging
import re
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from config import ANALYTICS_DB, OUTPUT_DIR, SOURCE_DB
from db import TunnelPool, fetch
from run_production_users_by_centre import (
    EXISTING_ID_CHUNK_SIZE,
    UUID_RE,
    fetch_result_for_id,
    get_ids,
    iter_parallel_results,
)

DEFAULT_SQL_PATH = Path("sql_queries/production_user_lesson_progress.sql")
DEFAULT_ADDON_TABLE = "user_addon"

# The allocation CTE drops non-teaching lesson types by default:
#   WHERE COALESCE(LOWER(TRIM(lesson_type)), '') NOT IN ('pdf', 'mp4', 'pdf web')
# --all-lesson-types / --exclude-lesson-types rewrite that one clause. The same
# clause appears in the subject-progress and main pipeline SQL, so this works for
# any of the three templates.
LESSON_TYPE_FILTER_RE = re.compile(
    r"WHERE\s+COALESCE\(LOWER\(TRIM\(lesson_type\)\),\s*''\)\s+NOT\s+IN\s*\([^)]*\)",
    re.IGNORECASE,
)


def apply_lesson_type_filter(sql: str, exclude: list[str] | None) -> str:
    """Rewrite the lesson-type exclusion. `exclude=[]` keeps every lesson type.

    Raises if the clause is not found exactly once — silently exporting filtered
    rows while reporting "all lesson types" would be worse than failing.
    """
    if exclude is None:
        return sql

    if exclude:
        quoted = ", ".join("'" + t.replace("'", "''") + "'" for t in exclude)
        replacement = f"WHERE COALESCE(LOWER(TRIM(lesson_type)), '') NOT IN ({quoted})"
    else:
        replacement = "WHERE 1 = 1  -- lesson-type filter disabled (--all-lesson-types)"

    updated, count = LESSON_TYPE_FILTER_RE.subn(replacement, sql, count=1)
    if count != 1:
        raise ValueError(
            "Could not find the lesson-type filter clause in the SQL template. "
            "Expected: WHERE COALESCE(LOWER(TRIM(lesson_type)), '') NOT IN (...)"
        )
    if exclude:
        logging.info("Lesson types excluded: %s", ", ".join(exclude))
    else:
        logging.info("Lesson-type filter disabled — exporting ALL lesson types")
    return updated

# Readable columns pulled from user_addon and moved to the front of the CSV.
ADDON_COLUMNS = [
    "username",
    "centre_name",
    "state_name",
    "district_name",
    "trade",
    "gender",
]


BATCH_SQL = """
SELECT b.id AS batch_id, b.name AS batch_name
FROM batches b
WHERE b.id IN ({placeholders})
"""

CENTRE_USER_SQL = """
SELECT u.id
FROM users u
WHERE u.centre_id = %s
  AND u.type IN (1, 2, 3, 4)
  AND u.status = 1
  AND u.deleted_at IS NULL
"""


def add_batch_name(df: pd.DataFrame) -> pd.DataFrame:
    """Resolve batch_name from the source `batches` table using batch_id.

    Read from source rather than user_addon so the name is correct even when the
    analytics addon table is stale. Unlike user_addon this does not filter out
    deleted/closed batches — for an extract, a name is more useful than a NULL.
    """
    if "batch_id" not in df.columns:
        logging.warning("No batch_id column in the result — cannot add batch_name.")
        return df

    batch_ids = sorted({b for b in df["batch_id"].dropna().astype(str) if b.strip()})
    if not batch_ids:
        logging.info("No batch_id values present; batch_name will be empty.")
        df["batch_name"] = None
        return df

    frames = []
    for start in range(0, len(batch_ids), EXISTING_ID_CHUNK_SIZE):
        chunk = batch_ids[start : start + EXISTING_ID_CHUNK_SIZE]
        placeholders = ", ".join(["%s"] * len(chunk))
        frames.append(fetch(SOURCE_DB, BATCH_SQL.format(placeholders=placeholders), tuple(chunk)))

    batches = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if batches.empty:
        logging.warning("No rows in `batches` matched these batch_ids.")
        df["batch_name"] = None
        return df

    batches["batch_id"] = batches["batch_id"].astype(str)
    df = df.copy()
    df["batch_id"] = df["batch_id"].astype(str).where(df["batch_id"].notna())
    merged = df.merge(batches.drop_duplicates("batch_id"), on="batch_id", how="left")
    logging.info(
        "Resolved %d batch name(s); %d of %d rows have one",
        len(batches), merged["batch_name"].notna().sum(), len(merged),
    )
    return merged


def resolve_centre_user_ids(centre_id: str) -> list[str]:
    """Active users of one centre — used by --centre-id --per-user."""
    df = fetch(SOURCE_DB, CENTRE_USER_SQL, (centre_id,))
    return [u for u in df[df.columns[0]].dropna().astype(str) if UUID_RE.match(u)]


def resolve_user_ids(user_sql_path: Path | None, inline_ids: list[str]) -> list[str]:
    """Collect user IDs from a SQL file and/or --user-id flags, keeping valid UUIDs."""
    ids: list[str] = []
    if user_sql_path is not None:
        ids.extend(get_ids(user_sql_path, None, None))
    ids.extend(inline_ids)

    valid, skipped = [], []
    for uid in dict.fromkeys(ids):          # de-duplicate, preserve order
        (valid if UUID_RE.match(uid.strip()) else skipped).append(uid.strip())

    for bad in skipped:
        logging.warning("Skipping invalid user id (not a UUID): %r", bad)
    return valid


def fetch_addon(user_ids: list[str], addon_table: str) -> pd.DataFrame:
    """Read the readable user attributes from the analytics DB, chunked."""
    frames = []
    for start in range(0, len(user_ids), EXISTING_ID_CHUNK_SIZE):
        chunk = user_ids[start : start + EXISTING_ID_CHUNK_SIZE]
        placeholders = ", ".join(["%s"] * len(chunk))
        sql = f"SELECT * FROM `{addon_table}` WHERE user_id IN ({placeholders})"
        frames.append(fetch(ANALYTICS_DB, sql, tuple(chunk)))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def enrich_with_addon(df: pd.DataFrame, addon_table: str) -> pd.DataFrame:
    """Left-join the readable user_addon columns and move them to the front.

    Never fatal: the export is worth having even if user_addon is missing or stale,
    so any failure here logs a warning and returns the frame unchanged.
    """
    try:
        addon = fetch_addon(sorted(df["user_id"].astype(str).unique()), addon_table)
    except Exception as exc:
        logging.warning("Could not read %s (%s) — writing CSV without it.", addon_table, exc)
        return df

    if addon.empty:
        logging.warning("No %s rows matched these users — writing CSV without it.", addon_table)
        return df

    keep = ["user_id"] + [c for c in ADDON_COLUMNS if c in addon.columns]
    missing = [c for c in ADDON_COLUMNS if c not in addon.columns]
    if missing:
        logging.warning("%s has no column(s): %s", addon_table, ", ".join(missing))

    addon = addon[keep].drop_duplicates(subset=["user_id"])
    merged = df.merge(addon, on="user_id", how="left")

    matched = merged[keep[1]].notna().sum() if len(keep) > 1 else 0
    logging.info("Enriched %d/%d rows from %s", matched, len(merged), addon_table)

    front = (
        ["user_id"]
        + [c for c in ADDON_COLUMNS if c in merged.columns]
        + [c for c in ("batch_name",) if c in merged.columns]
    )
    return merged[front + [c for c in merged.columns if c not in front]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export lesson-level detail for a list of users to CSV."
    )
    parser.add_argument(
        "--sql-path",
        type=Path,
        default=DEFAULT_SQL_PATH,
        help=f"SQL template to run per user. Default: {DEFAULT_SQL_PATH}",
    )
    parser.add_argument(
        "--user-sql-path",
        type=Path,
        default=None,
        help="SQL file returning user IDs in the first column.",
    )
    parser.add_argument(
        "--user-id",
        action="append",
        default=[],
        dest="user_ids",
        help="A single user UUID. Repeat for several. Combines with --user-sql-path.",
    )
    parser.add_argument(
        "--centre-id",
        default=None,
        help="Export every user of one centre. Runs a single centre-scoped query "
             "instead of one per user, which is far faster. Cannot be combined "
             "with --user-sql-path / --user-id.",
    )
    parser.add_argument(
        "--per-user",
        action="store_true",
        help="With --centre-id, resolve the centre's users and query them one at a "
             "time. Slower, but each query is small — use when a large centre fails "
             "with a MySQL temp-table error.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Output CSV path. Default: {OUTPUT_DIR}/user_lesson_progress_<timestamp>.csv",
    )
    parser.add_argument(
        "--addon-table",
        default=DEFAULT_ADDON_TABLE,
        help=f"Analytics table with readable user attributes. Default: {DEFAULT_ADDON_TABLE}",
    )
    parser.add_argument(
        "--no-addon",
        action="store_true",
        help="Skip the user_addon enrichment and export the raw SQL columns only.",
    )
    lesson_group = parser.add_mutually_exclusive_group()
    lesson_group.add_argument(
        "--all-lesson-types",
        action="store_true",
        help=(
            "Include every lesson type. By default the SQL drops "
            "'pdf', 'mp4' and 'pdf web' as non-teaching content."
        ),
    )
    lesson_group.add_argument(
        "--exclude-lesson-types",
        default=None,
        help=(
            "Comma-separated lesson types to drop instead of the SQL's default "
            "('pdf,mp4,pdf web'). Matched case-insensitively. Example: --exclude-lesson-types 'mp4'"
        ),
    )
    parser.add_argument(
        "--workers", type=int, default=4, help="Parallel source-query workers. Default: 4."
    )
    parser.add_argument(
        "--retries", type=int, default=2, help="Retries per failed user query. Default: 2."
    )
    args = parser.parse_args()

    if args.centre_id and (args.user_sql_path or args.user_ids):
        parser.error("--centre-id cannot be combined with --user-sql-path / --user-id")
    if args.centre_id and not UUID_RE.match(args.centre_id):
        parser.error(f"--centre-id is not a valid UUID: {args.centre_id}")
    if args.per_user and not args.centre_id:
        parser.error("--per-user only applies together with --centre-id")
    if not args.centre_id and args.user_sql_path is None and not args.user_ids:
        parser.error("Provide --centre-id, --user-sql-path, and/or --user-id")
    if args.workers < 1:
        parser.error("--workers must be 1 or greater")
    if args.retries < 0:
        parser.error("--retries must be 0 or greater")

    # None = leave the template's own filter alone; [] = keep every lesson type.
    if args.all_lesson_types:
        args.lesson_type_exclude = []
    elif args.exclude_lesson_types is not None:
        args.lesson_type_exclude = [
            t.strip().lower() for t in args.exclude_lesson_types.split(",") if t.strip()
        ]
        if not args.lesson_type_exclude:
            parser.error("--exclude-lesson-types was empty; use --all-lesson-types instead")
    else:
        args.lesson_type_exclude = None
    return args


def main() -> None:
    args = parse_args()

    out_path = args.out
    if out_path is None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = Path(OUTPUT_DIR) / f"user_lesson_progress_{stamp}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    sql_template = apply_lesson_type_filter(
        args.sql_path.read_text(), args.lesson_type_exclude
    )

    with TunnelPool() as pool:
        pool.open(SOURCE_DB)
        pool.open(ANALYTICS_DB)

        centre_single_query = bool(args.centre_id) and not args.per_user

        if args.centre_id:
            if args.per_user:
                user_ids = resolve_centre_user_ids(args.centre_id)
                if not user_ids:
                    logging.error("Centre %s has no active users.", args.centre_id)
                    raise SystemExit(1)
                logging.info(
                    "Centre %s: %d active users, one query each", args.centre_id, len(user_ids)
                )
            else:
                user_ids = []
                logging.info("Centre %s: single centre-scoped query", args.centre_id)
        else:
            user_ids = resolve_user_ids(args.user_sql_path, args.user_ids)
            if not user_ids:
                logging.error("No valid user ids to export.")
                raise SystemExit(1)
            logging.info("Exporting lesson detail for %d users", len(user_ids))

        logging.info("SQL template : %s", args.sql_path)
        logging.info("Output CSV   : %s", out_path)

        frames: list[pd.DataFrame] = []
        empty_users: list[str] = []
        failed_users: list[tuple[str, str]] = []

        def collect(index: int, user_id: str, result: pd.DataFrame, error) -> None:
            if error is not None:
                logging.error("User %s failed: %s", user_id, error)
                failed_users.append((user_id, str(error)))
            elif result.empty:
                logging.info("User %s returned no rows.", user_id)
                empty_users.append(user_id)
            else:
                logging.info("User %s: %d lesson rows", user_id, len(result))
                frames.append(result)

        if centre_single_query:
            _, _, result, error = fetch_result_for_id(
                sql_template, "centre", args.centre_id, 1, 1, 0, args.retries
            )
            if error is not None:
                logging.error("Centre %s failed: %s", args.centre_id, error)
                logging.error(
                    "If this is a MySQL temp-table error, retry with --per-user, which "
                    "splits the centre into one small query per user."
                )
                raise SystemExit(1)
            if result.empty:
                logging.error("Centre %s returned no rows.", args.centre_id)
                raise SystemExit(1)
            logging.info("Centre %s: %d lesson rows", args.centre_id, len(result))
            frames.append(result)
        elif args.workers == 1:
            for index, user_id in enumerate(user_ids, start=1):
                collect(
                    *fetch_result_for_id(
                        sql_template, "user", user_id, index, len(user_ids), 1, args.retries
                    )
                )
        else:
            for result in iter_parallel_results(
                sql_template, "user", user_ids, {}, args.workers, args.retries
            ):
                collect(*result)

        if not frames:
            logging.error("No rows returned for any user. Nothing written.")
            raise SystemExit(1)

        df = pd.concat(frames, ignore_index=True)
        df = add_batch_name(df)
        if not args.no_addon:
            df = enrich_with_addon(df, args.addon_table)

    df.to_csv(out_path, index=False)

    # ── Summary ──────────────────────────────────────────────────────────────
    logging.info("=" * 70)
    logging.info("Wrote %d rows x %d columns to %s", len(df), len(df.columns), out_path)
    logging.info("Users with data    : %d", df["user_id"].nunique())
    if "batch_name" in df.columns:
        logging.info("Distinct batches   : %d", df["batch_name"].nunique(dropna=True))
    if "lesson_type" in df.columns:
        counts = df["lesson_type"].fillna("(none)").value_counts()
        logging.info("Lesson types in CSV: %d", len(counts))
        for lesson_type, n in counts.items():
            logging.info("    %-24s %d", lesson_type, n)
    logging.info("Users with no rows : %d", len(empty_users))
    if empty_users:
        logging.info("  (no allocated lessons, or not active/type 1-4)")
        for uid in empty_users[:10]:
            logging.info("    %s", uid)
        if len(empty_users) > 10:
            logging.info("    ... %d more", len(empty_users) - 10)
    if failed_users:
        logging.error("Users FAILED       : %d — these are missing from the CSV", len(failed_users))
        for uid, err in failed_users[:10]:
            logging.error("    %s: %s", uid, err)
        raise SystemExit(1)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
