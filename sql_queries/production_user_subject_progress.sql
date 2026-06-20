/*
production_user_subject_progress.sql

Grain: one row per user-subject combination.

Returns subject-level allocation and completion counts for every allocated
user-subject pair, plus a JSON array of every lesson in that subject showing
each lesson's completion status, score, rating, and duration.

Use this when you need:
  - Subject-level completion rates per user
  - Which subjects a user has started vs not started
  - Lesson-level detail nested inside each subject row

Python runner injection: same params CTE as the main pipeline SQL.
Centre mode:  centre_id = <uuid>, user_id = NULL
User mode:    user_id   = <uuid>, centre_id = NULL
*/

WITH
params AS (
    SELECT
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS user_id,
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS centre_id,
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS batch_id,
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS subject_id,
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS trade_id,
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS project_id,
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS program_id,
        CAST(NULL AS CHAR(36)) COLLATE utf8mb4_unicode_ci AS phase_id
),

active_users AS (
    SELECT
        u.id               AS user_id,
        u.name             AS user_name,
        u.email,
        u.mobile,
        u.type             AS user_type,
        u.is_master_trainer,
        u.centre_id,
        u.project_id,
        u.organisation_id,
        u.is_ple,
        u.created_at,
        sd.batch_id,
        sd.trade_id,
        sd.educational_qualification_id,
        sd.placement_status_id,
        sd.active_year,
        sd.year_of_admission
    FROM users u
    CROSS JOIN params p
    LEFT JOIN student_details sd ON sd.user_id = u.id
    WHERE u.type IN (1, 2, 3, 4)
      AND u.status = 1
      AND u.deleted_at IS NULL
      AND (p.user_id   IS NULL OR u.id          = p.user_id)
      AND (p.centre_id IS NULL OR u.centre_id   = p.centre_id)
      AND (p.batch_id  IS NULL OR sd.batch_id   = p.batch_id)
      AND (p.trade_id  IS NULL OR sd.trade_id   = p.trade_id)
),

latest_career_path AS (
    SELECT user_id, job_type_id, updated_at AS career_path_updated_at
    FROM (
        SELECT
            pcpu.user_id,
            pcpu.job_type_id,
            pcpu.updated_at,
            ROW_NUMBER() OVER (
                PARTITION BY pcpu.user_id
                ORDER BY pcpu.updated_at DESC
            ) AS rn
        FROM ple_career_path_user pcpu
        WHERE pcpu.status = 1
          AND pcpu.deleted_at IS NULL
    ) ranked
    WHERE rn = 1
),

non_ple_allocation AS (
    SELECT
        u.user_id, u.user_name, u.user_type, u.centre_id, u.project_id,
        u.batch_id, u.trade_id,
        NULL                        AS career_path_id,
        NULL                        AS career_path_name,
        NULL                        AS career_path_updated_at,
        u.is_master_trainer,
        s.id                        AS subject_id,
        s.name                      AS subject_name,
        s.is_ple                    AS subject_is_ple,
        s.ple_career_path_id,
        s.year_to_map,
        cs.`order`                  AS subject_order,
        l.id                        AS lesson_id,
        l.name                      AS lesson_name,
        l.lesson_order,
        lt.name                     AS lesson_type,
        CASE WHEN l.is_assessment = 1 OR UPPER(l.name) LIKE '%ASSESSMENT%' THEN 1 ELSE 0 END AS is_assessment,
        CASE
            WHEN l.student_access      = 1 THEN 'student'
            WHEN l.facilitator_access  = 1 THEN 'facilitator'
            WHEN l.mastertrainer_access= 1 THEN 'master'
            ELSE NULL
        END                         AS toolkit_type,
        t_trade.duration            AS trade_duration,
        'non_ple'                   AS allocation_path,
        'centre_subject [-> batch_subject if batch] [-> subject_trade if trade]' AS allocation_basis
    FROM active_users u
    JOIN centre_subject cs   ON cs.centre_id = u.centre_id
    LEFT JOIN batch_subject bs
        ON u.batch_id IS NOT NULL AND bs.batch_id = u.batch_id AND bs.subject_id = cs.subject_id
    LEFT JOIN subject_trade st
        ON u.trade_id IS NOT NULL AND st.trade_id = u.trade_id AND st.subject_id = cs.subject_id
    LEFT JOIN trades t_trade ON t_trade.id = u.trade_id
    JOIN subjects s
        ON s.id = cs.subject_id AND s.status = 1 AND s.deleted_at IS NULL
    JOIN lessons l
        ON l.subject_id = s.id AND l.status = 1 AND l.deleted_at IS NULL
       AND l.student_access = 1
       AND l.lesson_category_id = 'd78bc322-568f-4110-8e24-02ea444d48b7'
    LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id
    WHERE u.user_type IN (3, 4)
      AND (u.is_ple IS NULL OR u.is_ple != 1)
      AND s.is_ple IN (0, 2)
      AND (u.batch_id IS NULL OR bs.subject_id IS NOT NULL)
      AND (u.trade_id IS NULL OR st.subject_id IS NOT NULL)
      AND (s.year_to_map IS NULL OR s.year_to_map = 0 OR t_trade.duration IS NULL OR s.year_to_map <= t_trade.duration)
      AND ((SELECT subject_id FROM params) IS NULL OR s.id = (SELECT subject_id FROM params))
),

ple_allocation AS (
    SELECT
        u.user_id, u.user_name, u.user_type, u.centre_id, u.project_id,
        u.batch_id,
        NULL                        AS trade_id,
        pcp.id                      AS career_path_id,
        pcp.name                    AS career_path_name,
        lcp.career_path_updated_at,
        u.is_master_trainer,
        s.id                        AS subject_id,
        s.name                      AS subject_name,
        s.is_ple                    AS subject_is_ple,
        s.ple_career_path_id,
        s.year_to_map,
        cs.`order`                  AS subject_order,
        l.id                        AS lesson_id,
        l.name                      AS lesson_name,
        l.lesson_order,
        lt.name                     AS lesson_type,
        CASE WHEN l.is_assessment = 1 OR UPPER(l.name) LIKE '%ASSESSMENT%' THEN 1 ELSE 0 END AS is_assessment,
        CASE
            WHEN l.student_access      = 1 THEN 'student'
            WHEN l.facilitator_access  = 1 THEN 'facilitator'
            WHEN l.mastertrainer_access= 1 THEN 'master'
            ELSE NULL
        END                         AS toolkit_type,
        t_trade.duration            AS trade_duration,
        'ple'                       AS allocation_path,
        'centre_subject [-> subject_ple_career_path if career_path] [-> batch_subject if batch]' AS allocation_basis
    FROM active_users u
    LEFT JOIN latest_career_path lcp ON lcp.user_id = u.user_id
    LEFT JOIN ple_career_paths pcp   ON pcp.id = lcp.job_type_id AND pcp.deleted_at IS NULL
    JOIN centre_subject cs           ON cs.centre_id = u.centre_id
    LEFT JOIN subject_ple_career_path spcp
        ON pcp.id IS NOT NULL AND spcp.ple_career_path_id = pcp.id AND spcp.subject_id = cs.subject_id
    LEFT JOIN batch_subject bs
        ON u.batch_id IS NOT NULL AND bs.batch_id = u.batch_id AND bs.subject_id = cs.subject_id
    LEFT JOIN trades t_trade ON t_trade.id = u.trade_id
    JOIN subjects s
        ON s.id = cs.subject_id AND s.status = 1 AND s.deleted_at IS NULL
    JOIN lessons l
        ON l.subject_id = s.id AND l.status = 1 AND l.deleted_at IS NULL
       AND l.student_access = 1
       AND l.lesson_category_id = 'd78bc322-568f-4110-8e24-02ea444d48b7'
    LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id
    WHERE u.user_type IN (3, 4)
      AND u.is_ple = 1
      AND s.is_ple IN (0, 1)
      AND pcp.id IS NOT NULL
      AND (s.is_ple = 0 OR spcp.subject_id IS NOT NULL)
      AND (u.batch_id IS NULL OR bs.subject_id IS NOT NULL)
      AND (s.year_to_map IS NULL OR s.year_to_map = 0 OR t_trade.duration IS NULL OR s.year_to_map <= t_trade.duration)
      AND ((SELECT subject_id FROM params) IS NULL OR s.id = (SELECT subject_id FROM params))
),

staff_allocation AS (
    SELECT
        u.user_id, u.user_name, u.user_type, u.centre_id, u.project_id,
        NULL                        AS batch_id,
        NULL                        AS trade_id,
        NULL                        AS career_path_id,
        NULL                        AS career_path_name,
        NULL                        AS career_path_updated_at,
        u.is_master_trainer,
        s.id                        AS subject_id,
        s.name                      AS subject_name,
        s.is_ple                    AS subject_is_ple,
        s.ple_career_path_id,
        s.year_to_map,
        cs.`order`                  AS subject_order,
        l.id                        AS lesson_id,
        l.name                      AS lesson_name,
        l.lesson_order,
        lt.name                     AS lesson_type,
        CASE WHEN l.is_assessment = 1 OR UPPER(l.name) LIKE '%ASSESSMENT%' THEN 1 ELSE 0 END AS is_assessment,
        CASE
            WHEN l.student_access      = 1 THEN 'student'
            WHEN l.facilitator_access  = 1 THEN 'facilitator'
            WHEN l.mastertrainer_access= 1 THEN 'master'
            ELSE NULL
        END                         AS toolkit_type,
        NULL                        AS trade_duration,
        'staff'                     AS allocation_path,
        'centre_subject (admin: all; facilitator: facilitator_access; master_trainer: mastertrainer_access)' AS allocation_basis
    FROM active_users u
    JOIN centre_subject cs ON cs.centre_id = u.centre_id
    JOIN subjects s
        ON s.id = cs.subject_id AND s.status = 1 AND s.deleted_at IS NULL
    JOIN lessons l
        ON l.subject_id = s.id AND l.status = 1 AND l.deleted_at IS NULL
       AND l.lesson_category_id = 'd78bc322-568f-4110-8e24-02ea444d48b7'
    LEFT JOIN lesson_types lt ON lt.id = l.lesson_type_id
    WHERE u.user_type IN (1, 2)
      AND s.is_ple IN (0, 1, 2)
      AND (
          u.user_type = 1
          OR (u.user_type = 2 AND (u.is_master_trainer IS NULL OR u.is_master_trainer != 1) AND l.facilitator_access  = 1)
          OR (u.user_type = 2 AND u.is_master_trainer = 1                                  AND l.mastertrainer_access = 1)
      )
      AND ((SELECT subject_id FROM params) IS NULL OR s.id = (SELECT subject_id FROM params))
),

allocation_union AS (
    SELECT * FROM non_ple_allocation
    UNION ALL
    SELECT * FROM ple_allocation
    UNION ALL
    SELECT * FROM staff_allocation
),

allocation_dedup AS (
    SELECT *
    FROM (
        SELECT
            au.*,
            ROW_NUMBER() OVER (
                PARTITION BY au.user_id, au.lesson_id
                ORDER BY au.career_path_updated_at DESC, au.lesson_order ASC
            ) AS rn
        FROM allocation_union au
    ) ranked
    WHERE rn = 1
),

allocation_filtered AS (
    SELECT * FROM allocation_dedup
    WHERE COALESCE(LOWER(TRIM(lesson_type)), '') NOT IN ('pdf', 'mp4', 'pdf web')
),

learner_completion AS (
    SELECT
        la.user_id,
        la.lesson_id,
        MAX(la.score)    AS score,
        MAX(la.rating)   AS rating,
        SUM(la.duration) AS duration
    FROM learning_activities la
    JOIN active_users u ON u.user_id = la.user_id AND u.user_type IN (3, 4)
    WHERE la.completed = 1
    GROUP BY la.user_id, la.lesson_id
),

staff_completion AS (
    SELECT
        fla.user_id,
        fla.lesson_id,
        MAX(fla.score)    AS score,
        MAX(fla.rating)   AS rating,
        SUM(fla.duration) AS duration
    FROM facilitator_learning_activities fla
    JOIN active_users u ON u.user_id = fla.user_id AND u.user_type NOT IN (3, 4)
    WHERE fla.completed = 1
    GROUP BY fla.user_id, fla.lesson_id
),

completion_dedup AS (
    SELECT user_id, lesson_id, score, rating, duration
    FROM (
        SELECT
            c.*,
            ROW_NUMBER() OVER (PARTITION BY c.user_id, c.lesson_id ORDER BY c.score DESC) AS rn
        FROM (
            SELECT * FROM learner_completion
            UNION ALL
            SELECT * FROM staff_completion
        ) c
    ) ranked
    WHERE rn = 1
),

-- ── merged: one row per user-lesson with completion flag ──────────────────────
merged AS (
    SELECT
        a.user_id,
        a.user_type,
        a.centre_id,
        a.project_id,
        a.batch_id,
        a.trade_id,
        a.career_path_id,
        a.career_path_name,
        a.is_master_trainer,
        a.subject_id,
        a.subject_name,
        a.subject_is_ple,
        a.year_to_map,
        a.subject_order,
        a.lesson_id,
        a.lesson_name,
        a.lesson_order,
        a.lesson_type,
        a.is_assessment,
        a.toolkit_type,
        a.allocation_path,
        a.allocation_basis,
        c.score,
        c.rating,
        c.duration,
        CASE WHEN c.user_id IS NULL THEN 0 ELSE 1 END AS completed
    FROM allocation_filtered a
    LEFT JOIN completion_dedup c
        ON c.user_id = a.user_id AND c.lesson_id = a.lesson_id
)

-- ── FINAL SELECT: one row per user-subject ────────────────────────────────────
-- Counts are at the subject level.
-- lessons_json contains every allocated lesson for this subject with its
-- completion status, score, rating, and duration.
SELECT
    m.user_id,
    au.organisation_id,
    m.user_type,
    m.centre_id,
    au.is_ple,
    m.batch_id,
    m.trade_id,
    m.career_path_id,
    m.career_path_name,
    m.subject_id,
    m.subject_name,
    m.subject_is_ple,
    m.year_to_map                                                                       AS year_category,
    m.subject_order,
    m.allocation_path,

    -- Subject-level allocation counts
    COUNT(m.lesson_id)                                                                  AS total_allocated,
    SUM(CASE WHEN m.is_assessment = 0 THEN 1 ELSE 0 END)                               AS lessons_allocated,
    SUM(CASE WHEN m.is_assessment = 1 THEN 1 ELSE 0 END)                               AS assessments_allocated,

    -- Subject-level completion counts
    SUM(m.completed)                                                                    AS total_completed,
    SUM(CASE WHEN m.completed = 1 AND m.is_assessment = 0 THEN 1 ELSE 0 END)           AS lessons_completed,
    SUM(CASE WHEN m.completed = 1 AND m.is_assessment = 1 THEN 1 ELSE 0 END)           AS assessments_completed,

    -- Subject completion percentage
    ROUND(SUM(m.completed) / NULLIF(COUNT(m.lesson_id), 0) * 100, 2)                  AS subject_completion_pct,

    -- Scores and time (only for completed lessons)
    ROUND(AVG(CASE WHEN m.completed = 1 THEN m.score    END), 2)                       AS avg_score,
    ROUND(AVG(CASE WHEN m.completed = 1 THEN m.rating   END), 2)                       AS avg_rating,
    SUM(m.duration)                                                                     AS total_duration_seconds,

    -- All lessons in this subject as a JSON array
    -- Each element: lesson identity, type, completion flag, and activity metrics
    JSON_ARRAYAGG(
        JSON_OBJECT(
            'lesson_id',     m.lesson_id,
            'lesson_name',   m.lesson_name,
            'lesson_order',  m.lesson_order,
            'lesson_type',   m.lesson_type,
            'is_assessment', m.is_assessment,
            'toolkit_type',  m.toolkit_type,
            'completed',     m.completed,
            'score',         m.score,
            'rating',        m.rating,
            'duration',      m.duration
        )
    )                                                                                   AS lessons_json

FROM merged m
JOIN active_users au ON au.user_id = m.user_id
GROUP BY
    m.user_id,
    au.organisation_id,
    m.user_type,
    m.centre_id,
    au.is_ple,
    m.batch_id,
    m.trade_id,
    m.career_path_id,
    m.career_path_name,
    m.subject_id,
    m.subject_name,
    m.subject_is_ple,
    m.year_to_map,
    m.subject_order,
    m.allocation_path
ORDER BY
    m.user_id,
    m.subject_order ASC;
