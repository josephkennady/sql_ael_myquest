#!/usr/bin/env bash
#
# CENTRE-WISE REFRESH WITH LAYERED RETRIES — built for cron.
#
# Refreshes every centre that had at least one user change in the last N days,
# one query per centre instead of one per user. Failed centres are retried in
# later sweeps, spaced apart so transient source-DB pressure has time to clear.
#
# Retry layers:
#   inner  --retries N   consecutive, ~2s apart, inside the runner
#   outer  SWEEPS        whole passes over whatever still failed, COOLDOWN apart
#
# Defaults give 3 attempts per sweep x 3 sweeps = 9 attempts per centre before
# it is finally reported as failed.
#
# Usage:
#   ./run_centre_refresh_cron.sh
#   SINCE_DAYS=7 ./run_centre_refresh_cron.sh
#   SWEEPS=4 COOLDOWN=1800 ./run_centre_refresh_cron.sh
#   WORKERS=1 ./run_centre_refresh_cron.sh      # for a source DB under pressure
#
# Environment overrides:
#   SINCE_DAYS   days of change to look back over        (default 5)
#   SWEEPS       outer passes over failed centres        (default 3)
#   RETRIES      inner retries, so attempts = RETRIES+1  (default 2 -> 3 attempts)
#   COOLDOWN     seconds between sweeps                  (default 600)
#   WORKERS      parallel source queries                 (default 2)
#   TARGET_TABLE analytics table                         (default production_users_one_record)
#   PYTHON       interpreter                             (default python3)
#   NO_EMAIL     set to 1 to skip the email report
#
# Why WORKERS defaults to 2, not 6: each centre query materialises the whole
# centre's allocation expansion in a MySQL internal temp table. Six of those at
# once exhausts the RDS temp space and every query dies with
# (1146, "Table './rdsdbdata/tmp/#sql...' doesn't exist"). Fewer, bigger queries
# beat more, failing ones.
#
# Exit code is 0 only when no centre is still failing after every sweep.

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
SINCE_DAYS="${SINCE_DAYS:-5}"
SWEEPS="${SWEEPS:-3}"
RETRIES="${RETRIES:-2}"
COOLDOWN="${COOLDOWN:-600}"
WORKERS="${WORKERS:-2}"
TARGET_TABLE="${TARGET_TABLE:-production_users_one_record}"
SQL_PATH="${SQL_PATH:-sql_queries/production_user_one_record_subject_project_combo.sql}"
CENTRE_SQL="${CENTRE_SQL:-sql_queries/changed_centres.sql}"

mkdir -p logs
STAMP="$(date +%Y-%m-%d_%H-%M-%S)"
LOG="logs/centre_refresh_${STAMP}.log"
SUCCEEDED_LIST="logs/centre_refresh_${STAMP}_succeeded_centres.txt"
FAILED_LIST="logs/centre_refresh_${STAMP}_failed_centres.txt"
START_MARKER="$(mktemp)"

stamp() { date "+%Y-%m-%d %H:%M:%S"; }

# ── Build the changed-centre list ────────────────────────────────────────────
# Date arithmetic lives in Python so the crontab needs no % escaping and the
# script behaves the same on GNU and BSD date.
generate_centre_sql() {
    "$PYTHON" - "$SINCE_DAYS" "$CENTRE_SQL" <<'PY'
import datetime, sys

days, out_path = int(sys.argv[1]), sys.argv[2]
cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(f"""-- Centres with at least one user changed since {cutoff}
-- Regenerated on every run; do not hand-edit.
SELECT DISTINCT u.centre_id AS id
FROM users u
JOIN (
    SELECT id AS user_id FROM users
     WHERE created_at >= '{cutoff}' OR updated_at >= '{cutoff}'
    UNION
    SELECT user_id FROM student_details
     WHERE created_at >= '{cutoff}' OR updated_at >= '{cutoff}'
    UNION
    SELECT user_id FROM learning_activities
     WHERE created_at >= '{cutoff}' OR updated_at >= '{cutoff}'
    UNION
    SELECT user_id FROM facilitator_learning_activities
     WHERE created_at >= '{cutoff}' OR updated_at >= '{cutoff}'
) changed ON changed.user_id = u.id
WHERE u.centre_id IS NOT NULL
  AND u.type IN (1, 2, 3, 4)
  AND u.status = 1
  AND u.deleted_at IS NULL
""")
print(cutoff)
PY
}

run_sweep() {
    local sweep="$1" centre_sql="$2"
    echo "======================================================================"
    echo "$(stamp) SWEEP $sweep/$SWEEPS — centre list: $centre_sql"
    echo "$(stamp) workers=$WORKERS retries=$RETRIES (=$((RETRIES + 1)) attempts this sweep)"
    echo "======================================================================"
    "$PYTHON" run_production_users_by_centre.py \
        --sql-path "$SQL_PATH" \
        --centre-sql-path "$centre_sql" \
        --target-table "$TARGET_TABLE" \
        --replace-existing-users \
        --workers "$WORKERS" \
        --retries "$RETRIES"
}

# Locate the retry file the runner wrote for this sweep. If the runner was killed
# before it could emit one, rebuild it from the partial failed-ids file.
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
    set -e
    local started; started=$(date +%s)

    echo "$(stamp) CENTRE REFRESH started"
    echo "$(stamp) Target table : $TARGET_TABLE"
    echo "$(stamp) Look back    : $SINCE_DAYS days"
    echo "$(stamp) Retry plan   : $SWEEPS sweeps x $((RETRIES + 1)) attempts = $((SWEEPS * (RETRIES + 1))) attempts per centre"
    echo "$(stamp) Cooldown     : ${COOLDOWN}s between sweeps"
    echo "$(stamp) Log file     : $LOG"

    local cutoff
    cutoff="$(generate_centre_sql)"
    echo "$(stamp) Cutoff       : $cutoff"
    echo "$(stamp) Centre list  : $CENTRE_SQL"

    local current_sql="$CENTRE_SQL" outstanding=0 sweep marker next_sql rc

    for sweep in $(seq 1 "$SWEEPS"); do
        marker="$(mktemp)"
        sleep 1   # ensure the marker predates anything this sweep writes

        rc=0
        run_sweep "$sweep" "$current_sql" || rc=$?

        if [ "$rc" -ne 0 ]; then
            echo "$(stamp) Sweep $sweep exited non-zero ($rc)"
            if [ "$sweep" -eq 1 ]; then
                echo "$(stamp) First sweep failed outright — aborting before downstream steps."
                rm -f "$marker"
                return "$rc"
            fi
        fi

        next_sql="$(find_retry_sql "$marker")"
        rm -f "$marker"

        if [ -z "$next_sql" ]; then
            echo "$(stamp) Sweep $sweep: no failed centres remaining."
            outstanding=0
            break
        fi

        outstanding="$(grep -c "^    '" "$next_sql" || true)"
        echo "$(stamp) Sweep $sweep: $outstanding centre(s) still failing -> $next_sql"
        current_sql="$next_sql"

        if [ "$sweep" -lt "$SWEEPS" ]; then
            echo "$(stamp) Cooling down ${COOLDOWN}s before sweep $((sweep + 1)) ..."
            sleep "$COOLDOWN"
        fi
    done

    if [ "$outstanding" -gt 0 ] && [ -n "$current_sql" ] && [ "$current_sql" != "$CENTRE_SQL" ]; then
        grep -oE "'[^']+'" "$current_sql" | tr -d "'" > "$FAILED_LIST"
    else
        : > "$FAILED_LIST"
    fi

    # ── Downstream steps ─────────────────────────────────────────────────────
    # These run even with centres outstanding: the snapshot is mostly refreshed
    # and the filter table should reflect it. The exit code still reports failure.
    echo "$(stamp) Running downstream steps"
    "$PYTHON" run_user_addon.py --target-table user_addon
    "$PYTHON" run_cleanup_inactive.py --target-table "$TARGET_TABLE"
    "$PYTHON" run_sql_filters.py --source-table "$TARGET_TABLE" --target-table sql_ael_filters

    local elapsed=$(( $(date +%s) - started ))
    printf '%s Finished in %02d:%02d:%02d\n' "$(stamp)" \
        $(( elapsed / 3600 )) $(( elapsed % 3600 / 60 )) $(( elapsed % 60 ))

    if [ "$outstanding" -gt 0 ]; then
        echo "$(stamp) $outstanding centre(s) STILL FAILING after $SWEEPS sweeps: $current_sql"
        echo "$(stamp) Retry by hand with a smaller --workers, or switch that centre to user mode:"
        echo "  $PYTHON run_production_users_by_centre.py --target-table $TARGET_TABLE \\"
        echo "    --incremental-users --centre-id <uuid> --since 1970-01-01 --workers 4"
        return 1
    fi
    echo "$(stamp) All centres refreshed successfully."
}

set +e
main 2>&1 | tee -a "$LOG"
status=${PIPESTATUS[0]}
set -e

if [ "$status" -eq 0 ]; then
    RESULT="SUCCESS"
else
    RESULT="FAILED"
fi
echo "$(stamp) CENTRE REFRESH $RESULT — log: $LOG" | tee -a "$LOG"

find logs -maxdepth 1 -name 'centre_ok_*.txt' -newer "$START_MARKER" -print0 2>/dev/null \
    | xargs -0 cat 2>/dev/null | sort -u > "$SUCCEEDED_LIST" || true
[ -f "$FAILED_LIST" ] || : > "$FAILED_LIST"
rm -f "$START_MARKER"

SUCCEEDED_COUNT=$(wc -l < "$SUCCEEDED_LIST" | tr -d ' ')
FAILED_COUNT=$(wc -l < "$FAILED_LIST" | tr -d ' ')
echo "$(stamp) Centres succeeded: $SUCCEEDED_COUNT | failed: $FAILED_COUNT" | tee -a "$LOG"

if [ "${NO_EMAIL:-0}" != "1" ]; then
    "$PYTHON" - "$RESULT" "$STAMP" "$LOG" "$SUCCEEDED_LIST" "$FAILED_LIST" \
        "$SUCCEEDED_COUNT" "$FAILED_COUNT" <<'PY' || echo "(email step failed, continuing)"
import sys
from pathlib import Path
from run_pipeline import send_email
result, stamp, log_path, ok_list, failed_list, ok_n, failed_n = sys.argv[1:8]
subject = f"[AEL Centre Refresh] {result} — {ok_n} ok / {failed_n} failed — {stamp}"
send_email(subject, Path(log_path), [Path(ok_list), Path(failed_list)])
PY
fi

exit "$status"
