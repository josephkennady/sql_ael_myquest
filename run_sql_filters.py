"""
Run sql_queries/sql_filter.sql against the analytics DB and write the result
to quest_analytics.sql_ael_filters.

The SQL expands subject_combos and project_combos JSON arrays using JSON_TABLE
and groups down to distinct filter dimension combinations for Superset.

Note: users with subject_combos = NULL (zero completions) are excluded because
CROSS JOIN JSON_TABLE on NULL returns no rows.

Usage:
    python3 run_sql_filters.py
    python3 run_sql_filters.py --source-table production_users_one_record
    python3 run_sql_filters.py --source-table production_users_one_record_test
    python3 run_sql_filters.py --target-table sql_ael_filters
    python3 run_sql_filters.py --append
"""

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from config import ANALYTICS_DB
from db import fetch, write_table

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

DEFAULT_SQL_PATH    = Path("sql_queries/sql_filter.sql")
DEFAULT_SOURCE_TABLE = "production_users_one_record"
DEFAULT_TARGET_TABLE = "sql_ael_filters"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run sql_filter.sql and write results to quest_analytics.sql_ael_filters."
    )
    parser.add_argument(
        "--sql-path",
        type=Path,
        default=DEFAULT_SQL_PATH,
        help=f"Path to the filter SQL file. Default: {DEFAULT_SQL_PATH}",
    )
    parser.add_argument(
        "--source-table",
        default=DEFAULT_SOURCE_TABLE,
        help=(
            f"Source table name inside quest_analytics to read from. "
            f"Default: {DEFAULT_SOURCE_TABLE}. "
            f"Use 'production_users_one_record' for production."
        ),
    )
    parser.add_argument(
        "--target-table",
        default=DEFAULT_TARGET_TABLE,
        help=f"Destination table name in quest_analytics. Default: {DEFAULT_TARGET_TABLE}",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append rows instead of replacing the table. Default: replace.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.sql_path.exists():
        logging.error("SQL file not found: %s", args.sql_path)
        sys.exit(1)

    sql = args.sql_path.read_text(encoding="utf-8")

    # Swap in the chosen source table (default is the test table)
    sql = sql.replace(
        "production_users_one_record_test",
        args.source_table,
    )

    if args.source_table != DEFAULT_SOURCE_TABLE:
        logging.info("Source table overridden → %s", args.source_table)

    logging.info("Reading filter SQL from %s", args.sql_path)
    logging.info("Source table : quest_analytics.%s", args.source_table)
    logging.info("Target table : quest_analytics.%s", args.target_table)

    logging.info("Running filter SQL against analytics DB ...")
    df = fetch(ANALYTICS_DB, sql)

    if df.empty:
        logging.warning(
            "Filter SQL returned 0 rows — nothing written. "
            "Check that %s has data and that subject_combos / project_combos are populated.",
            args.source_table,
        )
        sys.exit(0)

    logging.info("Filter SQL returned %d rows across %d columns", len(df), len(df.columns))
    logging.info("Columns: %s", list(df.columns))

    # Complex queries with CASE WHEN / JSON_TABLE return all columns as object
    # dtype in pandas regardless of the underlying MySQL type. Explicitly cast
    # every numeric column so _create_table_sql writes INT not TEXT.
    # NULL values become 0 as requested.
    int_columns = ["rounded_completion", "year_category", "batch_status"]
    for col in int_columns:
        if col in df.columns:
            df[col] = df[col].fillna(0).astype(int)
            logging.info("Cast column '%s' → int (nulls → 0)", col)

    if_exists = "append" if args.append else "replace"
    logging.info("Writing to quest_analytics.%s (mode: %s) ...", args.target_table, if_exists)

    write_table(ANALYTICS_DB, df, args.target_table, if_exists=if_exists)

    logging.info(
        "Done — %d rows written to quest_analytics.%s",
        len(df),
        args.target_table,
    )


if __name__ == "__main__":
    main()
