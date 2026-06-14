import argparse
import logging
from pathlib import Path

from config import ANALYTICS_DB, SOURCE_DB
from db import TunnelPool, fetch, write_table


DEFAULT_SQL_PATH = Path("sql_queries/user_addon.sql")
DEFAULT_TARGET_TABLE = "user_addon"


def run(sql_path: Path, target_table: str, if_exists: str) -> None:
    sql = sql_path.read_text()

    with TunnelPool() as pool:
        pool.open(SOURCE_DB)
        pool.open(ANALYTICS_DB)

        logging.info("Fetching user addon data from source...")
        result = fetch(SOURCE_DB, sql)
        logging.info("Fetched %d rows", len(result))

        if result.empty:
            logging.warning("Query returned no rows. Nothing written to %s.", target_table)
            return

        write_table(ANALYTICS_DB, result, target_table, if_exists=if_exists)
        logging.info("Wrote %d rows to %s", len(result), target_table)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run user_addon.sql and write results to the analytics DB."
    )
    parser.add_argument(
        "--sql-path",
        type=Path,
        default=DEFAULT_SQL_PATH,
        help=f"Path to the SQL file. Default: {DEFAULT_SQL_PATH}",
    )
    parser.add_argument(
        "--target-table",
        default=DEFAULT_TARGET_TABLE,
        help=f"Destination table name. Default: {DEFAULT_TARGET_TABLE}",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to the target table instead of replacing it.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    run(
        sql_path=args.sql_path,
        target_table=args.target_table,
        if_exists="append" if args.append else "replace",
    )
