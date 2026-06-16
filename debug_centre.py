"""
debug_centre.py — trace pipeline processing for a single centre at each CTE stage.

Shows exactly where data is being dropped: active users → subject mapping →
lesson eligibility → non-PLE allocation → PLE allocation → completions →
analytics table.

Usage:
    python3 debug_centre.py --centre-id 72ecca4e-80e3-43ca-ac81-77b72ae04c34
"""

import argparse
import sys

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from config import ANALYTICS_DB, SOURCE_DB
from db import TunnelPool, _connect_or_pool, fetch

LESSON_CATEGORY_ID = "d78bc322-568f-4110-8e24-02ea444d48b7"


# ─────────────────────────────────────────────────────────────────────────────
# Output helpers
# ─────────────────────────────────────────────────────────────────────────────

def hdr(n: int, title: str) -> None:
    bar = "─" * 72
    print(f"\n{bar}")
    print(f"  STAGE {n}: {title}")
    print(f"{bar}")


def warn(msg: str) -> None:
    print(f"  [!!] {msg}")


def ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def info(msg: str) -> None:
    print(f"       {msg}")


def show(df: pd.DataFrame, sample: int = 15) -> None:
    if df.empty:
        print("  (no rows)")
        return
    with pd.option_context(
        "display.max_columns", None,
        "display.width", 120,
        "display.max_colwidth", 50,
    ):
        print(df.head(sample).to_string(index=False))
        if len(df) > sample:
            print(f"  ... ({len(df) - sample} more rows not shown)")


# ─────────────────────────────────────────────────────────────────────────────
# Main debug runner
# ─────────────────────────────────────────────────────────────────────────────

def run(centre_id: str) -> None:
    print(f"\n{'='*72}")
    print(f"  PIPELINE DEBUG — centre: {centre_id}")
    print(f"{'='*72}")

    warnings: list[str] = []

    def flag(msg: str) -> None:
        warn(msg)
        warnings.append(msg)

    with TunnelPool() as pool:
        pool.open(SOURCE_DB)
        pool.open(ANALYTICS_DB)

        def src(sql: str, params=None) -> pd.DataFrame:
            return fetch(SOURCE_DB, sql, params)

        def ana(sql: str, params=None) -> pd.DataFrame:
            return fetch(ANALYTICS_DB, sql, params)

        # ── Stage 0: Centre existence ──────────────────────────────────────────
        hdr(0, "Centre existence and status")
        df = src(
            "SELECT id, name, status, deleted_at FROM centres WHERE id = %s",
            (centre_id,),
        )
        show(df)
        if df.empty:
            flag("Centre not found in source DB — check the UUID")
            _print_summary(warnings)
            return
        r = df.iloc[0]
        if r["status"] != 1:
            flag(f"Centre status={r['status']} (not 1) — centre is inactive")
        elif r["deleted_at"] is not None:
            flag(f"Centre deleted_at={r['deleted_at']} — centre is soft-deleted")
        else:
            ok(f"Centre '{r['name']}' is active")

        # ── Stage 1: Active users ──────────────────────────────────────────────
        hdr(1, "Active users  (status=1, deleted_at IS NULL, type IN 1-4)")
        df = src(
            """
            SELECT type, is_ple, COUNT(*) AS user_count
            FROM users
            WHERE centre_id = %s
              AND status = 1 AND deleted_at IS NULL AND type IN (1, 2, 3, 4)
            GROUP BY type, is_ple
            ORDER BY type, is_ple
            """,
            (centre_id,),
        )
        show(df)
        if df.empty:
            flag("No active users — pipeline will produce 0 rows")
        else:
            total = int(df["user_count"].sum())
            learners = int(df[df["type"].isin([3, 4])]["user_count"].sum())
            ple = int(df[(df["type"].isin([3, 4])) & (df["is_ple"] == 1)]["user_count"].sum())
            ok(f"Total active: {total}  |  Learners (type 3/4): {learners}  |  PLE learners: {ple}")
            if learners > 0 and ple == learners:
                flag("ALL learners are PLE (is_ple=1) — they only go through the PLE allocation path")
            elif ple > 0:
                info(f"{ple} learner(s) go via PLE path, {learners - ple} go via non-PLE path")

        # ── Stage 2: Centre subject mapping ───────────────────────────────────
        hdr(2, "Centre subject mapping  (centre_subject → subjects)")
        df = src(
            """
            SELECT s.name, s.status, s.deleted_at
            FROM centre_subject cs
            JOIN subjects s ON s.id = cs.subject_id
            WHERE cs.centre_id = %s
            ORDER BY s.name
            """,
            (centre_id,),
        )
        show(df)
        if df.empty:
            flag("No subjects mapped in centre_subject → zero allocation for all users")
        else:
            active = df[(df["status"] == 1) & (df["deleted_at"].isnull())]
            ok(f"Mapped: {len(df)}  |  Active: {len(active)}  |  Inactive/deleted: {len(df) - len(active)}")
            if len(active) == 0:
                flag("All mapped subjects are inactive/deleted → zero allocation")
            elif len(active) < len(df):
                flag(f"{len(df) - len(active)} subject(s) are inactive/deleted — excluded from allocation")

        # ── Stage 3: Eligible lessons ──────────────────────────────────────────
        hdr(3, f"Eligible lessons  (category={LESSON_CATEGORY_ID[:8]}…, student_access=1)")
        df = src(
            f"""
            SELECT s.name AS subject, lt.name AS lesson_type,
                   COUNT(l.id) AS lessons,
                   SUM(CASE WHEN COALESCE(LOWER(TRIM(lt.name)),'') NOT IN ('pdf','mp4','pdf web')
                       THEN 1 ELSE 0 END) AS after_type_filter
            FROM centre_subject cs
            JOIN subjects s ON s.id = cs.subject_id
              AND s.status = 1 AND s.deleted_at IS NULL
            JOIN lessons l ON l.subject_id = s.id
              AND l.status = 1 AND l.deleted_at IS NULL
              AND l.student_access = 1
              AND l.lesson_category_id = '{LESSON_CATEGORY_ID}'
            LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id
            WHERE cs.centre_id = %s
            GROUP BY s.name, lt.name
            ORDER BY s.name, lt.name
            """,
            (centre_id,),
        )
        show(df)
        if df.empty:
            flag(f"No lessons with lesson_category_id='{LESSON_CATEGORY_ID}' and student_access=1")
        else:
            after_filter = int(df["after_type_filter"].sum())
            ok(f"Lessons surviving pdf/mp4 type filter: {after_filter}")
            if after_filter == 0:
                flag("All eligible lessons are pdf/mp4/pdf web types — filtered out by allocation_filtered CTE")

        # ── Stage 4: Non-PLE allocation ────────────────────────────────────────
        hdr(4, "Non-PLE learner allocation  (is_ple=0, type 3/4)")
        df = src(
            f"""
            SELECT COUNT(DISTINCT u.id) AS eligible_users,
                   COUNT(DISTINCT l.id) AS eligible_lessons,
                   COUNT(*)             AS total_rows
            FROM users u
            JOIN centre_subject cs ON cs.centre_id = u.centre_id
            JOIN subjects s ON s.id = cs.subject_id
              AND s.status = 1 AND s.deleted_at IS NULL
            JOIN lessons l ON l.subject_id = s.id
              AND l.status = 1 AND l.deleted_at IS NULL
              AND l.student_access = 1
              AND l.lesson_category_id = '{LESSON_CATEGORY_ID}'
            LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id
            LEFT JOIN student_details sd ON sd.user_id = u.id
            LEFT JOIN batch_subject bs ON bs.batch_id = sd.batch_id
              AND bs.subject_id = cs.subject_id
            WHERE u.centre_id = %s
              AND u.status = 1 AND u.deleted_at IS NULL
              AND u.type IN (3, 4) AND u.is_ple = 0
              AND COALESCE(LOWER(TRIM(lt.name)),'') NOT IN ('pdf','mp4','pdf web')
              AND (sd.batch_id IS NULL OR bs.subject_id IS NOT NULL)
            """,
            (centre_id,),
        )
        show(df)
        non_ple_users = int(df.iloc[0]["eligible_users"]) if not df.empty else 0
        if non_ple_users == 0:
            flag("Non-PLE allocation: 0 users (all learners are PLE, or batch filter is excluding them)")
        else:
            ok(f"Non-PLE: {non_ple_users} users × {int(df.iloc[0]['eligible_lessons'])} lessons")

        # ── Stage 5: PLE allocation deep-dive ─────────────────────────────────
        hdr(5, "PLE allocation  (is_ple=1, type 3/4) — step-by-step")

        df_total = src(
            """
            SELECT COUNT(*) AS total_ple_learners FROM users
            WHERE centre_id = %s AND status = 1 AND deleted_at IS NULL
              AND is_ple = 1 AND type IN (3, 4)
            """,
            (centre_id,),
        )
        total_ple = int(df_total.iloc[0]["total_ple_learners"]) if not df_total.empty else 0
        info(f"PLE learners in centre: {total_ple}")

        # ple_career_path_user uses job_type_id (FK to ple_career_paths.id)
        df_cp = src(
            """
            SELECT COUNT(DISTINCT u.id) AS with_career_path
            FROM users u
            JOIN ple_career_path_user pcpu ON pcpu.user_id = u.id
              AND pcpu.status = 1 AND pcpu.deleted_at IS NULL
            JOIN ple_career_paths pcp ON pcp.id = pcpu.job_type_id
              AND pcp.deleted_at IS NULL
            WHERE u.centre_id = %s AND u.status = 1 AND u.deleted_at IS NULL
              AND u.is_ple = 1 AND u.type IN (3, 4)
            """,
            (centre_id,),
        )
        with_cp = int(df_cp.iloc[0]["with_career_path"]) if not df_cp.empty else 0
        info(f"With valid career path (ple_career_paths): {with_cp}")

        # PLE subjects require s.is_ple IN (1,2) — check if any centre subjects qualify
        df_ple_subj_count = src(
            """
            SELECT COUNT(*) AS ple_subjects
            FROM centre_subject cs
            JOIN subjects s ON s.id = cs.subject_id
              AND s.status = 1 AND s.deleted_at IS NULL AND s.is_ple IN (0, 1)
            WHERE cs.centre_id = %s
            """,
            (centre_id,),
        )
        ple_subj_count = int(df_ple_subj_count.iloc[0]["ple_subjects"]) if not df_ple_subj_count.empty else 0
        info(f"Centre subjects eligible for PLE path (is_ple IN 0,1): {ple_subj_count}")
        if ple_subj_count == 0:
            flag("No subjects have is_ple=0 or 1 — PLE allocation will produce 0 rows")

        df_subj = src(
            """
            SELECT COUNT(DISTINCT u.id) AS with_subject_mapping
            FROM users u
            JOIN ple_career_path_user pcpu ON pcpu.user_id = u.id
              AND pcpu.status = 1 AND pcpu.deleted_at IS NULL
            JOIN ple_career_paths pcp ON pcp.id = pcpu.job_type_id
              AND pcp.deleted_at IS NULL
            JOIN centre_subject cs ON cs.centre_id = u.centre_id
            JOIN subjects s ON s.id = cs.subject_id
              AND s.status = 1 AND s.deleted_at IS NULL AND s.is_ple IN (0, 1)
            LEFT JOIN subject_ple_career_path spcp ON spcp.ple_career_path_id = pcp.id
              AND spcp.subject_id = cs.subject_id
            WHERE u.centre_id = %s AND u.status = 1 AND u.deleted_at IS NULL
              AND u.is_ple = 1 AND u.type IN (3, 4)
              AND (s.is_ple = 0 OR spcp.subject_id IS NOT NULL)
            """,
            (centre_id,),
        )
        with_subj = int(df_subj.iloc[0]["with_subject_mapping"]) if not df_subj.empty else 0
        info(f"With career path + eligible subject (is_ple IN 0,1): {with_subj}")

        if total_ple == 0:
            ok("No PLE learners — PLE path not relevant for this centre")
        elif with_cp == 0:
            flag(f"All {total_ple} PLE learners have NO valid career path in ple_career_paths → zero PLE allocation")
        elif with_subj == 0:
            flag(
                f"{with_cp}/{total_ple} PLE learners have career paths "
                f"BUT subject_ple_career_path has NO matching entries → zero PLE allocation"
            )
            df_paths = src(
                """
                SELECT pcp.name AS career_path, COUNT(DISTINCT pcpu.user_id) AS users
                FROM ple_career_path_user pcpu
                JOIN ple_career_paths pcp ON pcp.id = pcpu.job_type_id
                  AND pcp.deleted_at IS NULL
                JOIN users u ON u.id = pcpu.user_id
                  AND u.centre_id = %s AND u.status = 1 AND u.deleted_at IS NULL AND u.is_ple = 1
                WHERE pcpu.status = 1 AND pcpu.deleted_at IS NULL
                GROUP BY pcp.name
                ORDER BY users DESC
                """,
                (centre_id,),
            )
            info("Career paths in use (need subject_ple_career_path entries):")
            show(df_paths)
            info("FIX option A: add subject mappings to subject_ple_career_path for these career paths")
            info("FIX option B: set is_ple=0 for these users if they are not actually PLE users")
        elif with_subj < total_ple:
            flag(f"{total_ple - with_subj} PLE learners have career paths with no matching subject mappings")
        else:
            ok(f"PLE allocation fully configured: {with_subj} users have complete career path + subject mapping")

        # ── Stage 6: Staff allocation ──────────────────────────────────────────
        hdr(6, "Staff allocation  (type 1/2)")
        df = src(
            f"""
            SELECT COUNT(DISTINCT u.id) AS staff_users,
                   COUNT(DISTINCT l.id) AS eligible_lessons
            FROM users u
            JOIN centre_subject cs ON cs.centre_id = u.centre_id
            JOIN subjects s ON s.id = cs.subject_id
              AND s.status = 1 AND s.deleted_at IS NULL
            JOIN lessons l ON l.subject_id = s.id
              AND l.status = 1 AND l.deleted_at IS NULL
              AND l.student_access = 1
              AND l.lesson_category_id = '{LESSON_CATEGORY_ID}'
            LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id
            WHERE u.centre_id = %s AND u.status = 1 AND u.deleted_at IS NULL
              AND u.type IN (1, 2)
              AND COALESCE(LOWER(TRIM(lt.name)),'') NOT IN ('pdf','mp4','pdf web')
            """,
            (centre_id,),
        )
        show(df)
        staff_users = int(df.iloc[0]["staff_users"]) if not df.empty else 0
        if staff_users == 0:
            info("No staff users with active allocation")
        else:
            ok(f"Staff allocation: {staff_users} users × {int(df.iloc[0]['eligible_lessons'])} lessons")

        # ── Stage 7: Learning activity completions ─────────────────────────────
        hdr(7, "Learning activity completions  (learning_activities table)")
        df = src(
            """
            SELECT la.completed, COUNT(*) AS records
            FROM learning_activities la
            JOIN users u ON u.id = la.user_id
            WHERE u.centre_id = %s AND u.status = 1 AND u.deleted_at IS NULL
            GROUP BY la.completed
            """,
            (centre_id,),
        )
        show(df)
        completed_rows = df[df["completed"] == 1]["records"].sum() if not df.empty else 0
        if completed_rows == 0:
            flag("No completed learning activities in learning_activities table")
        else:
            ok(f"{int(completed_rows)} completed activity records found")

        # ── Stage 8: Completions matching full allocation join ─────────────────
        hdr(8, "Completions matching full allocation chain")
        df = src(
            f"""
            SELECT COUNT(*) AS matched_completions
            FROM learning_activities la
            JOIN users u ON u.id = la.user_id
            JOIN lessons l ON l.id = la.lesson_id
            JOIN centre_subject cs ON cs.subject_id = l.subject_id
              AND cs.centre_id = u.centre_id
            JOIN subjects s ON s.id = cs.subject_id
              AND s.status = 1 AND s.deleted_at IS NULL
            LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id
            WHERE u.centre_id = %s AND u.status = 1 AND u.deleted_at IS NULL
              AND la.completed = 1
              AND l.lesson_category_id = '{LESSON_CATEGORY_ID}'
              AND COALESCE(LOWER(TRIM(lt.name)),'') NOT IN ('pdf','mp4','pdf web')
              AND l.status = 1 AND l.deleted_at IS NULL
              AND l.student_access = 1
            """,
            (centre_id,),
        )
        show(df)
        matched = int(df.iloc[0]["matched_completions"]) if not df.empty else 0
        if matched == 0 and completed_rows > 0:
            flag("Completions exist but none match the allocation chain — lesson category or subject mismatch")
        elif matched > 0:
            ok(f"{matched} completions will be captured when pipeline runs")

        # ── Stage 9: Current analytics table ──────────────────────────────────
        hdr(9, "Current analytics table  (production_users_one_record in ANALYTICS DB)")
        df = ana(
            """
            SELECT COUNT(*) AS row_count,
                   COALESCE(SUM(total_allocated), 0)  AS sum_allocated,
                   COALESCE(SUM(total_completed), 0)  AS sum_completed,
                   COALESCE(ROUND(AVG(completion_pct), 2), 0) AS avg_pct
            FROM production_users_one_record
            WHERE centre_id = %s
            """,
            (centre_id,),
        )
        show(df)
        if not df.empty:
            r = df.iloc[0]
            if r["row_count"] == 0:
                flag("No rows in analytics table — pipeline has not run for this centre")
            elif r["sum_completed"] == 0:
                flag("Rows exist but sum_completed=0 — completions are not being matched in SQL")
            else:
                ok(f"{int(r['row_count'])} rows, {int(r['sum_completed'])} completions, avg {r['avg_pct']}% completion")

    # ── Summary ──────────────────────────────────────────────────────────────
    _print_summary(warnings)


def _print_summary(warnings: list[str]) -> None:
    print(f"\n{'='*72}")
    print("  SUMMARY")
    print(f"{'='*72}")
    if not warnings:
        print("  No issues found — data looks correct for this centre.")
    else:
        print(f"  {len(warnings)} issue(s) detected:\n")
        for i, w in enumerate(warnings, 1):
            print(f"  {i}. {w}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Debug pipeline processing stages for a single centre."
    )
    parser.add_argument(
        "--centre-id",
        required=True,
        help="Centre UUID to debug (e.g. 72ecca4e-80e3-43ca-ac81-77b72ae04c34)",
    )
    args = parser.parse_args()
    run(args.centre_id)


if __name__ == "__main__":
    main()
