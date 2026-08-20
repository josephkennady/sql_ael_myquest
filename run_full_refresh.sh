#!/usr/bin/env bash
#
# FULL REFRESH — rebuild the AEL analytics snapshot from scratch.
#
# Step 1 drops and recreates production_users_one_record, loading every centre
# from `SELECT c.id FROM centres c` (via --limit 0). Steps 2–4 then rebuild the
# downstream tables exactly as run_pipeline.py does.
#
#   1. production_users_one_record   DROP + rebuild, all centres   (--replace-target)
#   1.N retry sweeps                 re-run only the centres that failed
#   2. user_addon                    DROP + rebuild
#   3. cleanup inactive              DELETE inactive users/centres
#   4. sql_ael_filters               DROP + rebuild
#
# Any failed step aborts the run — downstream tables are never built on top of a
# half-loaded snapshot.
#
# Retry sweeps: the runner exits 0 even when individual centres fail, and because
# --replace-target dropped the table up front, a failed centre is MISSING rather
# than stale. So after the rebuild the script re-runs whatever failed, up to
# SWEEPS times, COOLDOWN apart. Sweeps use --replace-existing-users, never
# --replace-target, so they can never discard what earlier passes loaded.
# If centres are still failing when the budget runs out, steps 2-4 still run (so
# the downstream tables match the snapshot) but the script exits 1.
#
# Usage:
#   ./run_full_refresh.sh                    # prompts before dropping the table
#   ./run_full_refresh.sh -y                 # skip the prompt (unattended / cron)
#   WORKERS=2 ./run_full_refresh.sh -y          # gentler on the source DB
#   SWEEPS=5 COOLDOWN=1800 ./run_full_refresh.sh -y
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
SWEEPS="${SWEEPS:-3}"
RETRIES="${RETRIES:-2}"
COOLDOWN="${COOLDOWN:-600}"

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

# Locate the retry file the runner wrote for the pass that started after $1.
# If the runner was killed before emitting one, rebuild it from the partial
# failed-ids file so a crash still leaves something to retry.
find_retry_sql() {
    local marker="$1" retry failed
    retry="$(find logs -maxdepth 1 -name 'centre_retry_*.sql' -newer "$marker" 2>/dev/null | sort | tail -1)"
    if [ -n "$retry" ]; then
        echo "$retry"
        return
    fi
    failed="$(find logs -maxdepth 1 -name 'centre_failed_*.txt' -newer "$marker" 2>/dev/null | sort | tail -1)"
    if [ -n "$failed" ] && [ -s "$failed" ]; then
        retry="logs/centre_retry_rebuilt_$(date +%Y-%m-%d_%H-%M-%S).sql"
        { echo "SELECT id FROM centres WHERE id IN ("
          cut -f1 "$failed" | sed "s/.*/    '&',/" | sed '$ s/,$//'
          echo ")"; } > "$retry"
        echo "$retry"
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
    echo "$(stamp) Retry plan   : initial pass + up to $SWEEPS retry sweep(s), $((RETRIES + 1)) attempts each"
    echo "$(stamp)                (worst case $(( (SWEEPS + 1) * (RETRIES + 1) )) attempts per centre)"
    echo "$(stamp) Cooldown     : ${COOLDOWN}s between sweeps"
    echo "$(stamp) Log file     : $LOG"

    local marker; marker="$(mktemp)"; sleep 1

    run_step "1. Full centre refresh" \
        "$PYTHON" run_production_users_by_centre.py \
        --sql-path "$SQL_PATH" \
        --target-table "$TARGET_TABLE" \
        --limit 0 \
        --replace-target \
        --retries "$RETRIES" \
        --workers "$WORKERS"

    # ── Retry sweeps ─────────────────────────────────────────────────────
    # run_production_users_by_centre.py exits 0 even when individual centres
    # fail, and --replace-target dropped the table up front, so a failed centre
    # is MISSING rather than stale. Sweep until it is clean or SWEEPS is spent.
    #
    # Sweeps use --replace-existing-users, never --replace-target: re-dropping
    # the table would discard everything the previous passes just loaded.
    local sweep=0 retry_sql
    OUTSTANDING=0
    while :; do
        # Re-checked after every pass, so the count always reflects the latest one.
        retry_sql="$(find_retry_sql "$marker")"
        rm -f "$marker"

        if [ -z "$retry_sql" ]; then
            OUTSTANDING=0
            if [ "$sweep" -eq 0 ]; then
                echo "$(stamp) Initial pass: every centre succeeded."
            else
                echo "$(stamp) Clean after retry sweep $sweep — snapshot is complete."
            fi
            break
        fi

        OUTSTANDING="$(grep -c "^    '" "$retry_sql" || true)"
        echo "$(stamp) $OUTSTANDING centre(s) failing -> $retry_sql"

        if [ "$sweep" -ge "$SWEEPS" ]; then
            echo "$(stamp) Retry budget of $SWEEPS sweep(s) exhausted."
            break
        fi

        sweep=$((sweep + 1))
        echo "$(stamp) Cooling down ${COOLDOWN}s before retry sweep $sweep ..."
        sleep "$COOLDOWN"

        marker="$(mktemp)"; sleep 1
        run_step "1.$sweep Retry $OUTSTANDING failed centre(s)" \
            "$PYTHON" run_production_users_by_centre.py \
            --sql-path "$SQL_PATH" \
            --target-table "$TARGET_TABLE" \
            --centre-sql-path "$retry_sql" \
            --replace-existing-users \
            --retries "$RETRIES" \
            --workers "$WORKERS"
    done

    rm -f "$marker"

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

    if [ "${OUTSTANDING:-0}" -gt 0 ]; then
        echo "$(stamp) WARNING: $OUTSTANDING centre(s) STILL FAILING after $SWEEPS sweeps."
        echo "$(stamp) Those centres have NO rows in $TARGET_TABLE — the table was rebuilt."
        echo "$(stamp) Retry them by hand with a lower --workers, or in user mode:"
        echo "  $PYTHON run_production_users_by_centre.py --target-table $TARGET_TABLE \\"
        echo "    --incremental-users --centre-id <uuid> --since 1970-01-01 --workers 4"
        return 1
    fi
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
