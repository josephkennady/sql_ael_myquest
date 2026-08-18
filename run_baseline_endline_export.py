"""
run_baseline_endline_export.py — baseline/endline assessment responses to CSV.

Runs sql_queries/baseline_endline_responses.sql against the source DB and writes
one CSV. Grain: one row per user / assessment stage / question, with the selected
options normalised to English. Read-only — nothing is written to any database.

Unlike the lesson export, this SQL is set-based: one query covers every user, so
there is no per-user loop and no --workers.

Restricting to a user list is the common case. The SQL's own population filters
(registration window 2025-09-01..2026-06-30, user type 3/4) are then redundant and
can silently exclude the very users you asked for, so they are DROPPED by default
when a user list is supplied. Pass --keep-population-filters to apply them anyway.

The centre-type restriction applies at two INNER JOIN sites; --centre-type sets
both together (default 'iti', as in the original SQL) and --all-centre-types drops
it. Run --check first: it prints the centre-type distribution of your users, which
is nearly always the reason an export comes back empty.

Usage:
    # Check which of your users can possibly appear, before exporting
    python3 run_baseline_endline_export.py --check

    # Export for the users in sql_queries/user_ids.sql
    python3 run_baseline_endline_export.py \
        --out output/s2sd_baseline_endline.csv

    # Everyone who took either stage, flagged by attempt_status (the default)
    python3 run_baseline_endline_export.py --centre-type ngo \
        --out output/s2sd_ngo_baseline_endline.csv

    # Only users with both stages (the original behaviour)
    python3 run_baseline_endline_export.py --centre-type ngo --paired-only

    # VTI centres instead of the default ITI
    python3 run_baseline_endline_export.py \
        --centre-type vti \
        --out output/s2sd_baseline_endline.csv

    # Keep the SQL's registration-window and user-type filters as well
    python3 run_baseline_endline_export.py --keep-population-filters

    # The original query — every eligible ITI user, no user list
    python3 run_baseline_endline_export.py --all-users --gzip \
        --out output/v2_baseline_endline.csv.gz
"""

import argparse
import datetime
import logging
import re
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from config import OUTPUT_DIR, SOURCE_DB
from db import TunnelPool, fetch
from run_user_lesson_export import resolve_user_ids

DEFAULT_SQL_PATH = Path("sql_queries/baseline_endline_responses.sql")
DEFAULT_USER_SQL_PATH = Path("sql_queries/user_ids.sql")

USER_FILTER_MARKER = "/*USER_FILTER*/"
POPULATION_MARKER = "/*POPULATION_TYPE*/"
PAIRED_ONLY_MARKER = "/*PAIRED_ONLY*/"

BASELINE_IDS = (
    "'bfac6a33-9013-43f5-a085-87fbf1ec95c1', "
    "'0dc96821-9489-4558-85e4-88fa8bd84875', "
    "'5456ddce-d62f-49cf-b23d-01557a60e8d6'"
)
ENDLINE_IDS = (
    "'06d3e617-59d6-419d-9a5d-9ac2d9d94498', "
    "'6d35233c-6aaa-4bc1-b72a-5b22604235c7', "
    "'6152b91c-d7b8-4f5c-babe-ddf32e624699'"
)
CENTRE_TYPE_MARKERS = {
    # marker -> the table alias in scope at that join site
    "/*CENTRE_TYPE_CANDIDATES*/": "registered_centre_type",
    "/*CENTRE_TYPE_FINAL*/": "ct",
}
DEFAULT_CENTRE_TYPES = ["iti"]

# centre_types.type values are short codes; refuse anything that is not one so a
# value can never break out of the SQL string it is interpolated into.
CENTRE_TYPE_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# The population conditions the SQL applies by default, as written in the original.
POPULATION_CONDITIONS = """AND registered_user.type IN (3, 4)
       AND registered_user.created_at >= '2025-09-01 00:00:00'
       AND registered_user.created_at <  '2026-07-01 00:00:00'"""

# Per-filter survivor counts for a user list — run before the export so an empty
# result is explained rather than mysterious.
CHECK_SQL = """
SELECT
    COUNT(*)                                                          AS users_found,
    SUM(u.deleted_at IS NULL)                                         AS not_deleted,
    SUM(u.type IN (3, 4))                                             AS type_3_or_4,
    SUM(u.created_at >= '2025-09-01 00:00:00'
        AND u.created_at < '2026-07-01 00:00:00')                     AS in_registration_window,
    SUM({centre_cond})                                                AS matching_centre_type,
    SUM(u.deleted_at IS NULL AND {centre_cond})                       AS reachable_by_default,
    SUM(u.deleted_at IS NULL AND {centre_cond} AND u.type IN (3, 4)
        AND u.created_at >= '2025-09-01 00:00:00'
        AND u.created_at < '2026-07-01 00:00:00')                     AS reachable_with_population
FROM quest_rearch_production.users u
LEFT JOIN quest_rearch_production.centres c
    ON c.id = u.centre_id
LEFT JOIN quest_rearch_production.centre_types ct
    ON c.centre_type_id = ct.id COLLATE utf8mb4_unicode_ci
WHERE u.id IN ({placeholders})
"""

# What centre types the requested users actually sit at — the first thing to look at
# when an export comes back empty.
CENTRE_TYPE_DISTRIBUTION_SQL = """
SELECT COALESCE(ct.type, '(no centre type)') AS centre_type,
       COUNT(*)                              AS users
FROM quest_rearch_production.users u
LEFT JOIN quest_rearch_production.centres c
    ON c.id = u.centre_id
LEFT JOIN quest_rearch_production.centre_types ct
    ON c.centre_type_id = ct.id COLLATE utf8mb4_unicode_ci
WHERE u.id IN ({placeholders})
GROUP BY ct.type
ORDER BY users DESC
"""

# One row per requested user with their baseline/endline status. Deliberately does
# NOT apply the centre-type or population filters: the point is to account for every
# user that was asked for, including the ones the main export cannot represent.
USER_STATUS_SQL = """
SELECT
    u.id                        AS user_id,
    u.name,
    u.email,
    u.mobile,
    u.gender,
    u.created_at                AS user_registered_at,
    c.name                      AS centre_name,
    ct.type                     AS centre_type_code,
    baseline.first_baseline_at,
    (SELECT MIN(COALESCE(r2.updated_at, r2.created_at))
       FROM quest_rearch_production.ple_assessment_responses r2
      WHERE r2.user_id = u.id
        AND r2.is_complete = 1
        AND r2.deleted_at IS NULL
        AND r2.assessment_id IN ({endline_ids})
        AND (baseline.first_baseline_at IS NULL
             OR COALESCE(r2.updated_at, r2.created_at) > baseline.first_baseline_at)
    )                           AS first_endline_at,
    CASE
        WHEN baseline.first_baseline_at IS NOT NULL
         AND (SELECT MIN(COALESCE(r3.updated_at, r3.created_at))
                FROM quest_rearch_production.ple_assessment_responses r3
               WHERE r3.user_id = u.id AND r3.is_complete = 1 AND r3.deleted_at IS NULL
                 AND r3.assessment_id IN ({endline_ids})
                 AND COALESCE(r3.updated_at, r3.created_at) > baseline.first_baseline_at
             ) IS NOT NULL THEN 'baseline_and_endline'
        WHEN baseline.first_baseline_at IS NOT NULL THEN 'baseline_only'
        WHEN EXISTS (SELECT 1
                       FROM quest_rearch_production.ple_assessment_responses r4
                      WHERE r4.user_id = u.id AND r4.is_complete = 1 AND r4.deleted_at IS NULL
                        AND r4.assessment_id IN ({endline_ids})
                    ) THEN 'endline_only'
        ELSE 'no_completed_attempt'
    END                         AS attempt_status
FROM quest_rearch_production.users u
LEFT JOIN quest_rearch_production.centres c
    ON c.id = u.centre_id
LEFT JOIN quest_rearch_production.centre_types ct
    ON c.centre_type_id = ct.id COLLATE utf8mb4_unicode_ci
LEFT JOIN (
    SELECT r.user_id,
           MIN(COALESCE(r.updated_at, r.created_at)) AS first_baseline_at
      FROM quest_rearch_production.ple_assessment_responses r
     WHERE r.is_complete = 1
       AND r.deleted_at IS NULL
       AND r.assessment_id IN ({baseline_ids})
     GROUP BY r.user_id
) baseline ON baseline.user_id = u.id
WHERE u.id IN ({placeholders})
ORDER BY attempt_status, u.name
"""

# How many of the selected users actually have completed baseline/endline attempts.
ATTEMPT_SQL = """
SELECT
    CASE r.assessment_id
        WHEN 'bfac6a33-9013-43f5-a085-87fbf1ec95c1' THEN 'baseline / start a business'
        WHEN '0dc96821-9489-4558-85e4-88fa8bd84875' THEN 'baseline / freelancer'
        WHEN '5456ddce-d62f-49cf-b23d-01557a60e8d6' THEN 'baseline / work in a company'
        WHEN '06d3e617-59d6-419d-9a5d-9ac2d9d94498' THEN 'endline  / start a business'
        WHEN '6d35233c-6aaa-4bc1-b72a-5b22604235c7' THEN 'endline  / freelancer'
        WHEN '6152b91c-d7b8-4f5c-babe-ddf32e624699' THEN 'endline  / work in a company'
    END                             AS assessment,
    COUNT(DISTINCT r.user_id)       AS users,
    COUNT(*)                        AS completed_attempts
FROM quest_rearch_production.ple_assessment_responses r
WHERE r.is_complete = 1
  AND r.deleted_at IS NULL
  AND r.assessment_id IN (
      'bfac6a33-9013-43f5-a085-87fbf1ec95c1', '0dc96821-9489-4558-85e4-88fa8bd84875',
      '5456ddce-d62f-49cf-b23d-01557a60e8d6', '06d3e617-59d6-419d-9a5d-9ac2d9d94498',
      '6d35233c-6aaa-4bc1-b72a-5b22604235c7', '6152b91c-d7b8-4f5c-babe-ddf32e624699')
  AND r.user_id IN ({placeholders})
GROUP BY r.assessment_id
ORDER BY assessment
"""


def _replace_marker(sql: str, marker: str, replacement: str) -> str:
    """Swap a marker exactly once, or fail — a silently unfiltered export is worse."""
    if sql.count(marker) != 1:
        raise ValueError(
            f"Expected exactly one {marker} in the SQL template, found {sql.count(marker)}."
        )
    return sql.replace(marker, replacement)


def build_sql(
    template: str,
    user_ids: list[str],
    keep_population: bool,
    centre_types: list[str] | None,
    paired_only: bool = False,
) -> str:
    """Inject the user filter, population conditions and centre-type restriction.

    `centre_types=None` means no restriction — the joins to centre_types stay
    (their columns are in the output) but every type is allowed through.
    """
    if user_ids:
        # UUIDs are validated by resolve_user_ids before reaching here.
        id_list = ",\n          ".join(f"'{uid}'" for uid in user_ids)
        user_clause = f"AND r.user_id IN (\n          {id_list}\n      )"
    else:
        user_clause = ""

    sql = _replace_marker(template, USER_FILTER_MARKER, user_clause)
    sql = _replace_marker(
        sql, POPULATION_MARKER, POPULATION_CONDITIONS if keep_population else ""
    )

    for marker, alias in CENTRE_TYPE_MARKERS.items():
        if centre_types:
            quoted = ", ".join(f"'{t}'" for t in centre_types)
            clause = f"AND {alias}.type IN ({quoted})"
        else:
            clause = ""
        sql = _replace_marker(sql, marker, clause)

    sql = _replace_marker(
        sql,
        PAIRED_ONLY_MARKER,
        "WHERE status.attempt_status = 'baseline_and_endline'" if paired_only else "",
    )
    return sql


def run_check(user_ids: list[str], centre_types: list[str] | None) -> None:
    placeholders = ", ".join(["%s"] * len(user_ids))

    if centre_types:
        quoted = ", ".join(f"'{t}'" for t in centre_types)
        centre_cond = f"ct.type IN ({quoted})"
        centre_label = f"centre type in ({', '.join(centre_types)})"
    else:
        centre_cond = "1"
        centre_label = "centre type (unrestricted)"

    # Distribution first — when an export is empty this is nearly always the reason.
    dist = fetch(
        SOURCE_DB, CENTRE_TYPE_DISTRIBUTION_SQL.format(placeholders=placeholders), tuple(user_ids)
    )
    logging.info("=" * 62)
    logging.info("CENTRE TYPES of the %d requested users", len(user_ids))
    logging.info("=" * 62)
    for r in dist.itertuples(index=False):
        marker = "  <-- selected" if centre_types and r.centre_type in centre_types else ""
        logging.info("  %-20s %4s users%s", r.centre_type, r.users, marker)

    counts = fetch(
        SOURCE_DB,
        CHECK_SQL.format(placeholders=placeholders, centre_cond=centre_cond),
        tuple(user_ids),
    )
    row = counts.iloc[0]

    logging.info("=" * 62)
    logging.info("POPULATION CHECK for %d requested user ids", len(user_ids))
    logging.info("=" * 62)
    logging.info("  found in users table       : %s", row["users_found"])
    logging.info("  not deleted                : %s", row["not_deleted"])
    logging.info("  type 3 or 4                : %s", row["type_3_or_4"])
    logging.info("  registered in window       : %s", row["in_registration_window"])
    logging.info("  %-27s: %s", centre_label, row["matching_centre_type"])
    logging.info("-" * 62)
    logging.info("  reachable (default)        : %s", row["reachable_by_default"])
    logging.info("  reachable (--keep-population-filters): %s", row["reachable_with_population"])

    if int(row["reachable_by_default"] or 0) == 0:
        logging.warning(
            "No requested user can appear in the export with %s. "
            "Pass --centre-type <code> (see the distribution above) or --all-centre-types.",
            centre_label,
        )
        return

    attempts = fetch(SOURCE_DB, ATTEMPT_SQL.format(placeholders=placeholders), tuple(user_ids))
    logging.info("-" * 62)
    if attempts.empty:
        logging.warning("None of these users has a completed baseline or endline attempt.")
        return
    logging.info("Completed attempts among the requested users:")
    for r in attempts.itertuples(index=False):
        logging.info("  %-32s users=%-5s attempts=%s", r.assessment, r.users, r.completed_attempts)
    logging.info(
        "A user needs a completed baseline AND a later completed endline to appear."
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export baseline/endline assessment responses to CSV."
    )
    parser.add_argument("--sql-path", type=Path, default=DEFAULT_SQL_PATH,
                        help=f"Assessment SQL template. Default: {DEFAULT_SQL_PATH}")
    parser.add_argument("--user-sql-path", type=Path, default=DEFAULT_USER_SQL_PATH,
                        help=f"SQL file returning user IDs. Default: {DEFAULT_USER_SQL_PATH}")
    parser.add_argument("--user-id", action="append", default=[], dest="user_ids",
                        help="A single user UUID. Repeat for several.")
    parser.add_argument("--all-users", action="store_true",
                        help="Ignore the user list and run the original unrestricted query.")
    parser.add_argument("--keep-population-filters", action="store_true",
                        help="Apply the SQL's user-type and registration-window filters "
                             "even when a user list is given. Always on with --all-users.")
    centre_group = parser.add_mutually_exclusive_group()
    centre_group.add_argument(
        "--centre-type", default=",".join(DEFAULT_CENTRE_TYPES),
        help="Comma-separated centre_types.type codes to include. "
             f"Default: {','.join(DEFAULT_CENTRE_TYPES)}. Example: --centre-type vti",
    )
    centre_group.add_argument(
        "--all-centre-types", action="store_true",
        help="Do not restrict by centre type at all.",
    )
    parser.add_argument("--paired-only", action="store_true",
                        help="Only users with BOTH a baseline and a later endline "
                             "(the original behaviour). Default includes baseline-only "
                             "and endline-only users, flagged by attempt_status.")
    parser.add_argument("--summary-out", type=Path, default=None,
                        help="Per-user status CSV covering every requested user, including "
                             "those with no completed attempt. Default: alongside --out as "
                             "<name>_user_status.csv. Use 'none' to skip.")
    parser.add_argument("--check", action="store_true",
                        help="Report how many users survive each filter, then exit. No export.")
    parser.add_argument("--out", type=Path, default=None,
                        help=f"Output CSV path. Default: {OUTPUT_DIR}/baseline_endline_<timestamp>.csv")
    parser.add_argument("--gzip", action="store_true",
                        help="Gzip the CSV (appends .gz if the path does not end with it).")
    parser.add_argument("--print-sql", action="store_true",
                        help="Print the assembled SQL and exit without running it.")
    args = parser.parse_args()

    if args.all_centre_types:
        args.centre_types = None
    else:
        args.centre_types = [t.strip().lower() for t in args.centre_type.split(",") if t.strip()]
        if not args.centre_types:
            parser.error("--centre-type was empty; use --all-centre-types instead")
        bad = [t for t in args.centre_types if not CENTRE_TYPE_RE.match(t)]
        if bad:
            parser.error(f"invalid centre type code(s): {', '.join(bad)}")
    return args


def main() -> None:
    args = parse_args()
    template = args.sql_path.read_text()

    out_path = args.out
    if out_path is None:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = Path(OUTPUT_DIR) / f"baseline_endline_{stamp}.csv"
    if args.gzip and out_path.suffix != ".gz":
        out_path = out_path.with_suffix(out_path.suffix + ".gz")

    with TunnelPool() as pool:
        pool.open(SOURCE_DB)

        user_ids: list[str] = []
        if not args.all_users:
            user_sql = args.user_sql_path if args.user_sql_path.exists() else None
            if user_sql is None and not args.user_ids:
                raise SystemExit(
                    f"{args.user_sql_path} not found. Create it, pass --user-id, or use --all-users."
                )
            user_ids = resolve_user_ids(user_sql, args.user_ids)
            if not user_ids:
                raise SystemExit("No valid user ids resolved.")
            logging.info("Restricting to %d user ids", len(user_ids))

        if args.check:
            if not user_ids:
                raise SystemExit("--check needs a user list; it is meaningless with --all-users.")
            run_check(user_ids, args.centre_types)
            return

        keep_population = args.keep_population_filters or args.all_users
        sql = build_sql(
            template, user_ids, keep_population, args.centre_types, args.paired_only
        )
        logging.info(
            "Population filters (type 3/4, registration window): %s",
            "APPLIED" if keep_population else "dropped — the user list is the filter",
        )
        logging.info(
            "Centre types: %s",
            ", ".join(args.centre_types) if args.centre_types else "ALL (unrestricted)",
        )

        if args.print_sql:
            print(sql)
            return

        logging.info(
            "Coverage: %s",
            "paired baseline+endline only"
            if args.paired_only
            else "all users with either stage (attempt_status column)",
        )

        # Per-user status first, so it exists even if the main query returns nothing.
        summary = pd.DataFrame()
        if user_ids and str(args.summary_out).lower() != "none":
            placeholders = ", ".join(["%s"] * len(user_ids))
            summary = fetch(
                SOURCE_DB,
                USER_STATUS_SQL.format(
                    placeholders=placeholders,
                    baseline_ids=BASELINE_IDS,
                    endline_ids=ENDLINE_IDS,
                ),
                tuple(user_ids),
            )

        logging.info("Running assessment query ...")
        df = fetch(SOURCE_DB, sql)

    if not summary.empty:
        summary_path = args.summary_out
        if summary_path is None:
            summary_path = out_path.with_name(
                out_path.name.replace(".csv.gz", "").replace(".csv", "") + "_user_status.csv"
            )
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary.to_csv(summary_path, index=False)
        logging.info("=" * 62)
        logging.info("Wrote per-user status for %d users to %s", len(summary), summary_path)
        for status, n in summary["attempt_status"].value_counts().items():
            logging.info("  %-22s %d users", status, n)
        found = set(summary["user_id"].astype(str))
        for missing in [u for u in user_ids if u not in found]:
            logging.warning("  requested user not found in users table: %s", missing)

    if df.empty:
        logging.warning("Query returned 0 rows — nothing written.")
        logging.warning("Run with --check to see which filter removes your users.")
        raise SystemExit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False, compression="gzip" if out_path.suffix == ".gz" else None)

    logging.info("=" * 62)
    logging.info("Wrote %d rows x %d columns to %s", len(df), len(df.columns), out_path)
    logging.info("Users in export : %d", df["user_id"].nunique())
    if "assessment_stage" in df.columns:
        for stage, n in df.groupby("assessment_stage")["user_id"].nunique().items():
            logging.info("  %-10s %d users", stage, n)
    if "attempt_status" in df.columns:
        for status, n in df.groupby("attempt_status")["user_id"].nunique().items():
            logging.info("  %-22s %d users", status, n)
    if user_ids:
        missing = len(user_ids) - df["user_id"].nunique()
        if missing:
            logging.warning(
                "%d of the %d requested users have no rows here — no completed attempt, "
                "or removed by the centre-type filter. Every requested user is accounted "
                "for in the per-user status CSV above.", missing, len(user_ids)
            )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    main()
