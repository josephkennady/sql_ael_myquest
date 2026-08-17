#!/usr/bin/env bash
#
# FULL REFRESH — rebuild the AEL analytics snapshot from scratch.
#
# Step 1 drops and recreates production_users_one_record, loading every centre
# from `SELECT c.id FROM centres c` (via --limit 0). Steps 2–4 then rebuild the
# downstream tables exactly as run_pipeline.py does.
#
#   1. production_users_one_record   DROP + rebuild, all centres   (--replace-target)
#   2. user_addon                    DROP + rebuild
#   3. cleanup inactive              DELETE inactive users/centres
#   4. sql_ael_filters               DROP + rebuild
#
# Any failed step aborts the run — downstream tables are never built on top of a
# half-loaded snapshot.
#
# Usage:
#   ./run_full_refresh.sh                    # prompts before dropping the table
#   ./run_full_refresh.sh -y                 # skip the prompt (unattended / cron)
#   WORKERS=8 ./run_full_refresh.sh
#   TARGET_TABLE=production_users_one_record_new ./run_full_refresh.sh -y
#
# Notes:
#   * --centre-sql-path is deliberately NOT used. sql_queries/centre_ids.sql is
#     gitignored local scratch and currently holds a single hardcoded centre;
#     passing it would refresh one centre instead of all of them.
#   * The target table is empty from the moment step 1 starts until it finishes.
#     To avoid that gap, set TARGET_TABLE to a staging table, let it build, then
#     RENAME TABLE it into place.
#   * Users with centre_id IS NULL are not reachable in centre mode — they are
#     only picked up by the incremental (per-user) path.
#   * No email is sent; the full log is written to logs/full_refresh_*.log.

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
WORKERS="${WORKERS:-6}"
TARGET_TABLE="${TARGET_TABLE:-production_users_one_record}"
SQL_PATH="${SQL_PATH:-sql_queries/production_user_one_record_subject_project_combo.sql}"
ADDON_TABLE="${ADDON_TABLE:-user_addon}"
FILTER_TABLE="${FILTER_TABLE:-sql_ael_filters}"

ASSUME_YES=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) ASSUME_YES=1 ;;
        -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $arg (use -y to skip the confirmation)" >&2; exit 2 ;;
    esac
done

mkdir -p logs
LOG="logs/full_refresh_$(date +%Y-%m-%d_%H-%M-%S).log"

stamp() { date "+%Y-%m-%d %H:%M:%S"; }

run_step() {
    local label="$1"; shift
    echo "======================================================================"
    echo "$(stamp) STEP START: $label"
    echo "$(stamp) Command:    $*"
    echo "======================================================================"
    if "$@"; then
        echo "$(stamp) STEP OK:     $label"
    else
        local rc=$?
        echo "$(stamp) STEP FAILED: $label (exit $rc)"
        return "$rc"
    fi
}

main() {
    # main runs in a subshell of the `| tee` pipeline, which inherits the `set +e`
    # used below to capture its exit status. Re-arm errexit here so a failed step
    # aborts the run instead of falling through to the next one.
    set -e

    local started
    started=$(date +%s)

    echo "$(stamp) FULL REFRESH started"
    echo "$(stamp) Target table : $TARGET_TABLE  (will be DROPPED and rebuilt)"
    echo "$(stamp) Snapshot SQL : $SQL_PATH"
    echo "$(stamp) Workers      : $WORKERS"
    echo "$(stamp) Log file     : $LOG"

    run_step "1. Full centre refresh" \
        "$PYTHON" run_production_users_by_centre.py \
        --sql-path "$SQL_PATH" \
        --target-table "$TARGET_TABLE" \
        --limit 0 \
        --replace-target \
        --workers "$WORKERS"

    run_step "2. User addon" \
        "$PYTHON" run_user_addon.py \
        --target-table "$ADDON_TABLE"

    run_step "3. Cleanup inactive" \
        "$PYTHON" run_cleanup_inactive.py \
        --target-table "$TARGET_TABLE"

    run_step "4. SQL filter table" \
        "$PYTHON" run_sql_filters.py \
        --source-table "$TARGET_TABLE" \
        --target-table "$FILTER_TABLE"

    local elapsed=$(( $(date +%s) - started ))
    printf '%s All 4 steps passed in %02d:%02d:%02d\n' \
        "$(stamp)" $(( elapsed / 3600 )) $(( elapsed % 3600 / 60 )) $(( elapsed % 60 ))
}

if [ "$ASSUME_YES" -ne 1 ]; then
    echo "This DROPS and rebuilds '$TARGET_TABLE' in the analytics DB."
    echo "The table will be empty or partial until the run finishes."
    printf "Type 'yes' to continue: "
    read -r reply
    if [ "$reply" != "yes" ]; then
        echo "Aborted."
        exit 1
    fi
fi

set +e
main 2>&1 | tee -a "$LOG"
status=${PIPESTATUS[0]}
set -e

if [ "$status" -eq 0 ]; then
    echo "$(stamp) FULL REFRESH SUCCESS — log: $LOG" | tee -a "$LOG"
else
    echo "$(stamp) FULL REFRESH FAILED (exit $status) — log: $LOG" | tee -a "$LOG"
fi

exit "$status"
