import argparse
import logging
import re
from pathlib import Path

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
    return ids[id_column].dropna().astype(str).tolist()


def run(
    sql_path: Path,
    centre_sql_path: Path | None,
    user_sql_path: Path | None,
    target_table: str,
    limit: int | None,
    replace_target: bool,
) -> None:
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

        for index, id_value in enumerate(ids, start=1):
            logging.info("Running %s %d/%d: %s", id_type, index, len(ids), id_value)
            scoped_sql = build_sql_for_id(sql_template, id_type, id_value)
            result = fetch(SOURCE_DB, scoped_sql)

            if result.empty:
                logging.info("%s %s returned no rows. Skipping write.", id_type.title(), id_value)
                continue

            if_exists = "append" if target_created else "replace"
            write_table(ANALYTICS_DB, result, target_table, if_exists=if_exists)
            target_created = True
            logging.info("Wrote %d rows for %s %s", len(result), id_type, id_value)


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
    return parser.parse_args()


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
    )
