#!/usr/bin/env bash
#
# INCREMENTAL REFRESH — the routine run. Wraps run_pipeline.py, which already
# handles logging, system monitoring and the email report.
#
#   1. production_users_one_record   per-user DELETE + reinsert for changed users
#   2. user_addon                    DROP + rebuild
#   3. cleanup inactive              DELETE inactive users/centres
#   4. sql_ael_filters               DROP + rebuild
#
# Step 1 refreshes users changed since MAX(created_at) in the snapshot (minus a
# 5 minute overlap), plus any active source user missing from the destination.
#
# Usage:
#   ./run_incremental_refresh.sh
#   ./run_incremental_refresh.sh --since-days 5     # pin the cutoff to 5 days back
#   ./run_incremental_refresh.sh --since 2026-08-06 # pin it to a fixed date
#   ./run_incremental_refresh.sh --no-email
#   ./run_incremental_refresh.sh --dry-run          # preview the cleanup deletes
#   WORKERS=8 ./run_incremental_refresh.sh
#   TARGET_TABLE=production_users_one_record_test ./run_incremental_refresh.sh
#
# For cron, prefer --since-days N over shell date arithmetic: it needs no %
# escaping in the crontab and behaves the same on Linux and macOS.
#
# Any extra arguments are passed straight through to run_pipeline.py.
#
# Note: if the target table does not exist, run_pipeline.py falls back to a full
# centre refresh driven by sql_queries/centre_ids.sql — which is gitignored local
# scratch and currently holds a single hardcoded centre. For a genuine rebuild
# use ./run_full_refresh.sh instead.

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
WORKERS="${WORKERS:-6}"
TARGET_TABLE="${TARGET_TABLE:-production_users_one_record}"

exec "$PYTHON" run_pipeline.py \
    --target-table "$TARGET_TABLE" \
    --workers "$WORKERS" \
    "$@"
