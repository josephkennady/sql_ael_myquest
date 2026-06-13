import argparse
import logging
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import pandas as pd
import pymysql

from config import ANALYTICS_DB, SOURCE_DB
from db import TunnelPool, fetch, write_table


DEFAULT_SQL_PATH = Path("sql_queries/production_user_one_record_subject_project_combo.sql")
DEFAULT_TARGET_TABLE = "production_users_one_record"
DEFAULT_CENTRE_QUERY = """
SELECT c.id
FROM centres c
"""


PARAM_REPLACEMENTS = {
    "user_id": "CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS user_id",
    "centre_id": "CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS centre_id",
    "batch_id": "CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS batch_id",
}

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
EXISTING_ID_CHUNK_SIZE = 1000
PROGRESS_BAR_WIDTH = 30


def _quote_identifier(identifier: str) -> str:
    if not IDENTIFIER_RE.match(identifier):
        raise ValueError(f"Invalid SQL identifier: {identifier}")
    return f"`{identifier}`"


def _replace_param(sql: str, param_name: str, replacement_expr: str) -> str:
    pattern = re.compile(
        rf"^\s*.+?\s+COLLATE\s+utf8mb4_unicode_ci\s+AS\s+{param_name}\s*,?\s*$",
        re.MULTILINE,
    )
    suffix = "," if param_name != "phase_id" else ""
    replacement = f"        {replacement_expr}{suffix}"
    updated, count = pattern.subn(replacement, sql, count=1)
    if count != 1:
        raise ValueError(f"Could not replace params.{param_name} in SQL template")
    return updated


def _uuid_expr(param_name: str, value: str) -> str:
    return f"_utf8mb4'{value}' COLLATE utf8mb4_unicode_ci AS {param_name}"


def build_sql_for_id(template_sql: str, id_type: str, id_value: str) -> str:
    if not UUID_RE.match(id_value):
        raise ValueError(f"Invalid {id_type} id: {id_value}")

    sql = template_sql
    sql = _replace_param(sql, "user_id", PARAM_REPLACEMENTS["user_id"])
    sql = _replace_param(sql, "centre_id", PARAM_REPLACEMENTS["centre_id"])
    sql = _replace_param(sql, "batch_id", PARAM_REPLACEMENTS["batch_id"])
    sql = _replace_param(sql, f"{id_type}_id", _uuid_expr(f"{id_type}_id", id_value))
    return sql


def get_ids(
    ids_sql_path: Path | None,
    limit: int | None,
    default_query: str | None,
) -> list[str]:
    if ids_sql_path is not None:
        sql = ids_sql_path.read_text()
    elif limit is not None:
        sql = f"""
SELECT c.id
FROM centres c
LIMIT {int(limit)}
"""
    elif default_query is not None:
        sql = default_query
    else:
        raise ValueError("An ID SQL path is required for this run mode")

    ids = fetch(SOURCE_DB, sql)
    id_column = "id" if "id" in ids.columns else ids.columns[0]
    return list(dict.fromkeys(ids[id_column].dropna().astype(str).tolist()))


def get_existing_ids(target_table: str, id_type: str, ids: list[str]) -> set[str]:
    table_sql = _quote_identifier(target_table)
    id_column_sql = _quote_identifier(f"{id_type}_id")
    existing_ids: set[str] = set()

    for start in range(0, len(ids), EXISTING_ID_CHUNK_SIZE):
        chunk = ids[start : start + EXISTING_ID_CHUNK_SIZE]
        placeholders = ", ".join(["%s"] * len(chunk))
        sql = f"""
SELECT DISTINCT {id_column_sql} AS id
FROM {table_sql}
WHERE {id_column_sql} IN ({placeholders})
"""
        try:
            existing = fetch(ANALYTICS_DB, sql, tuple(chunk))
        except pymysql.err.ProgrammingError as exc:
            if exc.args[0] == 1146:
                logging.info("Target table %s does not exist yet; no IDs to skip.", target_table)
                return set()
            raise

        existing_ids.update(existing["id"].dropna().astype(str).tolist())

    return existing_ids


def get_user_counts_by_id(id_type: str, ids: list[str]) -> dict[str, int]:
    user_counts: dict[str, int] = {id_value: 0 for id_value in ids}
    id_column = "centre_id" if id_type == "centre" else "id"

    for start in range(0, len(ids), EXISTING_ID_CHUNK_SIZE):
        chunk = ids[start : start + EXISTING_ID_CHUNK_SIZE]
        placeholders = ", ".join(["%s"] * len(chunk))
        sql = f"""
SELECT u.{id_column} AS id, COUNT(*) AS user_count
FROM users u
WHERE u.type IN (1, 2, 3, 4)
  AND u.status = 1
  AND u.deleted_at IS NULL
  AND u.{id_column} IN ({placeholders})
GROUP BY u.{id_column}
"""
        counts = fetch(SOURCE_DB, sql, tuple(chunk))
        for row in counts.itertuples(index=False):
            user_counts[str(row.id)] = int(row.user_count)

    return user_counts


def format_progress_bar(done: int, total: int, width: int = PROGRESS_BAR_WIDTH) -> str:
    if total <= 0:
        return "[" + "-" * width + "]"
    filled = min(width, int(width * done / total))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def fetch_result_for_id(
    sql_template: str,
    id_type: str,
    id_value: str,
    index: int,
    total: int,
    active_users: int,
    retries: int,
) -> tuple[int, str, pd.DataFrame, Exception | None]:
    logging.info(
        "Running %s %d/%d: %s (active users=%d)",
        id_type,
        index,
        total,
        id_value,
        active_users,
    )
    scoped_sql = build_sql_for_id(sql_template, id_type, id_value)
    for attempt in range(retries + 1):
        try:
            result = fetch(SOURCE_DB, scoped_sql)
            return index, id_value, result, None
        except Exception as exc:
            if attempt >= retries:
                return index, id_value, pd.DataFrame(), exc
            logging.warning(
                "%s %s failed on attempt %d/%d. Retrying. Error: %s",
                id_type.title(),
                id_value,
                attempt + 1,
                retries + 1,
                exc,
            )
            time.sleep(2)


def iter_parallel_results(
    sql_template: str,
    id_type: str,
    ids: list[str],
    user_counts: dict[str, int],
    workers: int,
    retries: int,
):
    id_iter = iter(enumerate(ids, start=1))
    total = len(ids)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = set()

        def submit_next() -> bool:
            try:
                index, id_value = next(id_iter)
            except StopIteration:
                return False
            pending.add(
                executor.submit(
                    fetch_result_for_id,
                    sql_template,
                    id_type,
                    id_value,
                    index,
                    total,
                    user_counts.get(id_value, 0),
                    retries,
                )
            )
            return True

        for _ in range(workers):
            if not submit_next():
                break

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future.result()
                submit_next()


def run(
    sql_path: Path,
    centre_sql_path: Path | None,
    user_sql_path: Path | None,
    target_table: str,
    limit: int | None,
    replace_target: bool,
    workers: int,
    skip_existing: bool,
    retries: int,
) -> None:
    if workers < 1:
        raise ValueError("--workers must be 1 or greater")
    if retries < 0:
        raise ValueError("--retries must be 0 or greater")
    if replace_target and skip_existing:
        raise ValueError("--skip-existing cannot be used with --replace-target")

    sql_template = sql_path.read_text()
    if user_sql_path is not None:
        id_type = "user"
        ids = get_ids(user_sql_path, None, None)
    else:
        id_type = "centre"
        ids = get_ids(centre_sql_path, limit, DEFAULT_CENTRE_QUERY)

    logging.info("Found %d %s ids", len(ids), id_type)
    if not ids:
        logging.warning("No %s ids found. Nothing to write.", id_type)
        return

    target_created = not replace_target

    with TunnelPool() as pool:
        pool.open(SOURCE_DB)
        pool.open(ANALYTICS_DB)

        if skip_existing:
            existing_ids = get_existing_ids(target_table, id_type, ids)
            if existing_ids:
                ids = [id_value for id_value in ids if id_value not in existing_ids]
                logging.info(
                    "Skipping %d existing %s ids from %s",
                    len(existing_ids),
                    id_type,
                    target_table,
                )
            if not ids:
                logging.info("All requested %s ids already exist. Nothing to write.", id_type)
                return

        user_counts = get_user_counts_by_id(id_type, ids)
        total_users = sum(user_counts.values())
        completed_ids = 0
        completed_users = 0
        logging.info(
            "Progress target: %d %s ids, %d active users",
            len(ids),
            id_type,
            total_users,
        )
        failed_ids: list[tuple[str, str]] = []

        def log_progress(id_value: str) -> None:
            nonlocal completed_ids, completed_users
            completed_ids += 1
            completed_users += user_counts.get(id_value, 0)
            id_pct = completed_ids / len(ids) * 100 if ids else 100
            user_pct = completed_users / total_users * 100 if total_users else 100
            logging.info(
                "Progress %s %s %d/%d (%.2f%%), users %d/%d (%.2f%%), current %s users=%d",
                format_progress_bar(completed_users, total_users),
                id_type,
                completed_ids,
                len(ids),
                id_pct,
                completed_users,
                total_users,
                user_pct,
                id_type,
                user_counts.get(id_value, 0),
            )

        def write_result(
            index: int,
            id_value: str,
            result: pd.DataFrame,
            error: Exception | None,
        ) -> None:
            nonlocal target_created
            if error is not None:
                logging.error(
                    "%s %s failed after %d attempt(s). Skipping and continuing. Error: %s",
                    id_type.title(),
                    id_value,
                    retries + 1,
                    error,
                )
                failed_ids.append((id_value, str(error)))
                log_progress(id_value)
                return

            if result.empty:
                logging.info("%s %s returned no rows. Skipping write.", id_type.title(), id_value)
                log_progress(id_value)
                return

            if_exists = "append" if target_created else "replace"
            logging.info(
                "Writing %d rows for %s %d/%d: %s",
                len(result),
                id_type,
                index,
                len(ids),
                id_value,
            )
            write_table(ANALYTICS_DB, result, target_table, if_exists=if_exists)
            target_created = True
            logging.info("Wrote %d rows for %s %s", len(result), id_type, id_value)
            log_progress(id_value)

        if workers == 1:
            for index, id_value in enumerate(ids, start=1):
                write_result(
                    *fetch_result_for_id(
                        sql_template,
                        id_type,
                        id_value,
                        index,
                        len(ids),
                        user_counts.get(id_value, 0),
                        retries,
                    )
                )
        else:
            logging.info("Using %d workers for %s reads", workers, id_type)
            for result in iter_parallel_results(
                sql_template,
                id_type,
                ids,
                user_counts,
                workers,
                retries,
            ):
                write_result(*result)

        if failed_ids:
            logging.error("%d %s ids failed and were skipped:", len(failed_ids), id_type)
            for failed_id, error in failed_ids[:25]:
                logging.error("  %s: %s", failed_id, error)
            if len(failed_ids) > 25:
                logging.error("  ... %d more failed ids", len(failed_ids) - 25)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run production user one-record SQL once per centre or user and write all "
            "results into one target table."
        )
    )
    parser.add_argument(
        "--sql-path",
        type=Path,
        default=DEFAULT_SQL_PATH,
        help=f"SQL template path. Default: {DEFAULT_SQL_PATH}",
    )
    parser.add_argument(
        "--target-table",
        default=DEFAULT_TARGET_TABLE,
        help=f"Destination table name. Default: {DEFAULT_TARGET_TABLE}",
    )
    id_group = parser.add_mutually_exclusive_group()
    id_group.add_argument(
        "--centre-sql-path",
        type=Path,
        default=None,
        help="Optional SQL file that returns centre IDs in the first column.",
    )
    id_group.add_argument(
        "--user-sql-path",
        type=Path,
        default=None,
        help="Optional SQL file that returns user IDs in the first column.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help=(
            "Number of centres to process when no ID SQL file is provided. "
            "Use 0 to process all centres."
        ),
    )
    parser.add_argument(
        "--replace-target",
        action="store_true",
        help="Recreate the target table on the first non-empty result.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel source-query workers. Default: 1.",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip IDs already present in the target table. Checks centre_id in "
            "centre mode and user_id in user mode. Cannot be used with --replace-target."
        ),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=1,
        help="Number of retries for a failed centre/user source query. Default: 1.",
    )
    args = parser.parse_args()
    if args.replace_target and args.skip_existing:
        parser.error("--skip-existing cannot be used with --replace-target")
    return args


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args()
    run(
        sql_path=args.sql_path,
        centre_sql_path=args.centre_sql_path,
        user_sql_path=args.user_sql_path,
        target_table=args.target_table,
        limit=None if args.limit == 0 else args.limit,
        replace_target=args.replace_target,
        workers=args.workers,
        skip_existing=args.skip_existing,
        retries=args.retries,
    )
