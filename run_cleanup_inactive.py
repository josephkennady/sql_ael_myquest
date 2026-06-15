"""
Delete rows from the analytics snapshot table (production_users_one_record)
where the user or their centre has become inactive / soft-deleted in the
production source database.

Flow:
  1. Query SOURCE_DB  → collect inactive user IDs and inactive centre IDs
  2. Query ANALYTICS_DB → delete matching rows from production_users_one_record

Usage:
    python3 run_cleanup_inactive.py
    python3 run_cleanup_inactive.py --target-table production_users_one_record
    python3 run_cleanup_inactive.py --dry-run
"""

import argparse
import logging

from config import ANALYTICS_DB, SOURCE_DB
from db import _connect_or_pool, fetch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

INACTIVE_USERS_SQL = """
SELECT id
FROM users
WHERE status != 1
   OR deleted_at IS NOT NULL
"""

INACTIVE_CENTRES_SQL = """
SELECT id
FROM centres
WHERE status != 1
   OR deleted_at IS NOT NULL
"""


def get_inactive_ids(sql: str, label: str) -> list[str]:
    df = fetch(SOURCE_DB, sql)
    ids = df["id"].dropna().astype(str).tolist()
    logging.info("Found %d inactive %s in source DB", len(ids), label)
    return ids


def delete_in_chunks(
    target_table: str,
    column: str,
    ids: list[str],
    dry_run: bool,
) -> int:
    if not ids:
        logging.info("No inactive %s IDs — nothing to delete.", column)
        return 0

    chunk_size = 500
    total_deleted = 0

    with _connect_or_pool(ANALYTICS_DB) as conn:
        with conn.cursor() as cur:
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i : i + chunk_size]
                placeholders = ", ".join(["%s"] * len(chunk))
                sql = f"DELETE FROM `{target_table}` WHERE `{column}` IN ({placeholders})"

                if dry_run:
                    logging.info(
                        "[DRY RUN] Would delete rows where %s IN (%d ids, e.g. %s...)",
                        column,
                        len(chunk),
                        chunk[0],
                    )
                else:
                    cur.execute(sql, chunk)
                    total_deleted += cur.rowcount

        if not dry_run:
            conn.commit()

    return total_deleted


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove inactive users/centres from analytics snapshot.")
    parser.add_argument(
        "--target-table",
        default="production_users_one_record",
        help="Analytics table to clean up (default: production_users_one_record)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without making any changes",
    )
    args = parser.parse_args()

    if args.dry_run:
        logging.info("DRY RUN mode — no rows will be deleted")

    inactive_user_ids = get_inactive_ids(INACTIVE_USERS_SQL, "users")
    inactive_centre_ids = get_inactive_ids(INACTIVE_CENTRES_SQL, "centres")

    deleted_users = delete_in_chunks(
        args.target_table, "user_id", inactive_user_ids, args.dry_run
    )
    deleted_centres = delete_in_chunks(
        args.target_table, "centre_id", inactive_centre_ids, args.dry_run
    )

    if args.dry_run:
        logging.info(
            "DRY RUN complete — %d inactive user IDs, %d inactive centre IDs found",
            len(inactive_user_ids),
            len(inactive_centre_ids),
        )
    else:
        logging.info(
            "Cleanup complete — deleted %d rows by user ID, %d rows by centre ID",
            deleted_users,
            deleted_centres,
        )


if __name__ == "__main__":
    main()
