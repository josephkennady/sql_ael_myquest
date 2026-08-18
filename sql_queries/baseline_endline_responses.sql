/*
Purpose: Return English-normalized question responses for ITI users who
completed any configured baseline and any configured endline. The two
assessments do not need to belong to the same career-pathway pair.

Translation mapping:
  - Questions are matched by their ordered position within the same assessment
    because the schema has no direct translation-link ID.
  - Selected options are matched by option sort_order within the matched
    English question.
  - Free-text answers cannot be translated from database data, so they remain
    exactly as entered by the user.

Multiple attempts:
  - Select the user's earliest completed baseline across all three pathways.
  - Select the user's earliest completed endline after that baseline.
  - Question answers come from those exact selected attempts.

Coverage:
  - Users with only one stage are included and flagged via attempt_status
    ('baseline_and_endline' | 'baseline_only' | 'endline_only').
  - PAIRED_ONLY restores the original both-stages-required behaviour.
  - Users with no completed attempt at all have no question rows to emit and
    therefore cannot appear here; see the --summary-out per-user CSV.

Population filters:
  - User registration: 2025-09-01 through 2026-06-30.
  - User type: 3 or 4.
  - Centre type: iti (overridable with --centre-type / --all-centre-types).

Runner injection (run_baseline_endline_export.py):
  - USER_FILTER      -> AND r.user_id IN (...)      restricts to a user list
  - POPULATION_TYPE  -> user type / registration-window conditions
  - CENTRE_TYPE_*    -> centre_types.type restriction, in both join sites
    Both are replaced with an empty string when not restricted, leaving this
    file's default behaviour unchanged.
*/

WITH assessment_set AS (
    SELECT
        'baseline' AS assessment_stage,
        'I want to start a business' AS pathway,
        'bfac6a33-9013-43f5-a085-87fbf1ec95c1' AS assessment_id

    UNION ALL

    SELECT
        'baseline',
        'I want to work as a Freelancer',
        '0dc96821-9489-4558-85e4-88fa8bd84875'

    UNION ALL

    SELECT
        'baseline',
        'I want to work in a company',
        '5456ddce-d62f-49cf-b23d-01557a60e8d6'

    UNION ALL

    SELECT
        'endline',
        'I want to start a business',
        '06d3e617-59d6-419d-9a5d-9ac2d9d94498'

    UNION ALL

    SELECT
        'endline',
        'I want to work as a Freelancer',
        '6d35233c-6aaa-4bc1-b72a-5b22604235c7'

    UNION ALL

    SELECT
        'endline',
        'I want to work in a company',
        '6152b91c-d7b8-4f5c-babe-ddf32e624699'
),

completed_candidates AS (
    SELECT
        r.id AS assessment_response_id,
        r.user_id,
        r.assessment_id,
        configured_assessment.assessment_stage,
        configured_assessment.pathway,
        r.created_at AS response_created_at,
        COALESCE(r.updated_at, r.created_at) AS completed_at,
        r.final_score,
        r.rating
    FROM quest_rearch_production.ple_assessment_responses AS r
    INNER JOIN assessment_set AS configured_assessment
        ON configured_assessment.assessment_id = r.assessment_id
    INNER JOIN quest_rearch_production.users AS registered_user
        ON registered_user.id = r.user_id
       AND registered_user.deleted_at IS NULL
       /*POPULATION_TYPE*/
    INNER JOIN quest_rearch_production.centres AS registered_centre
        ON registered_centre.id = registered_user.centre_id
    INNER JOIN quest_rearch_production.centre_types AS registered_centre_type
        ON registered_centre.centre_type_id = registered_centre_type.id
           COLLATE utf8mb4_unicode_ci
       /*CENTRE_TYPE_CANDIDATES*/
    WHERE r.is_complete = 1
      AND r.deleted_at IS NULL
      /*USER_FILTER*/
),

ranked_baselines AS (
    SELECT
        candidate.*,
        ROW_NUMBER() OVER (
            PARTITION BY candidate.user_id
            ORDER BY candidate.completed_at, candidate.assessment_response_id
        ) AS baseline_rank
    FROM completed_candidates AS candidate
    WHERE candidate.assessment_stage = 'baseline'
),

first_baseline AS (
    SELECT *
    FROM ranked_baselines
    WHERE baseline_rank = 1
),

/*
Earliest completed endline. When the user has a baseline it must come after it —
an endline recorded before the baseline is not a valid pairing, so such a user is
reported as baseline_only. Users with no baseline at all keep their earliest
endline, which is what makes endline_only possible.
*/
ranked_endlines AS (
    SELECT
        endline.*,
        ROW_NUMBER() OVER (
            PARTITION BY endline.user_id
            ORDER BY endline.completed_at, endline.assessment_response_id
        ) AS endline_rank
    FROM completed_candidates AS endline
    LEFT JOIN first_baseline AS baseline
        ON baseline.user_id = endline.user_id
    WHERE endline.assessment_stage = 'endline'
      AND (baseline.user_id IS NULL
           OR endline.completed_at > baseline.completed_at)
),

first_endline AS (
    SELECT *
    FROM ranked_endlines
    WHERE endline_rank = 1
),

/* One row per user per stage that actually exists. */
user_stage_attempts AS (
    SELECT
        baseline.user_id,
        'baseline' AS assessment_stage,
        baseline.pathway,
        baseline.assessment_id,
        baseline.assessment_response_id,
        baseline.response_created_at,
        baseline.completed_at,
        baseline.final_score,
        baseline.rating
    FROM first_baseline AS baseline

    UNION ALL

    SELECT
        endline.user_id,
        'endline',
        endline.pathway,
        endline.assessment_id,
        endline.assessment_response_id,
        endline.response_created_at,
        endline.completed_at,
        endline.final_score,
        endline.rating
    FROM first_endline AS endline
),

user_status AS (
    SELECT
        user_id,
        MAX(assessment_stage = 'baseline') AS has_baseline,
        MAX(assessment_stage = 'endline')  AS has_endline,
        CASE
            WHEN MAX(assessment_stage = 'baseline') = 1
             AND MAX(assessment_stage = 'endline')  = 1 THEN 'baseline_and_endline'
            WHEN MAX(assessment_stage = 'baseline') = 1 THEN 'baseline_only'
            ELSE 'endline_only'
        END AS attempt_status
    FROM user_stage_attempts
    GROUP BY user_id
),

paired_attempts AS (
    SELECT
        stage_attempt.pathway,
        baseline.pathway AS baseline_pathway,
        endline.pathway  AS endline_pathway,
        stage_attempt.user_id,
        status.attempt_status,
        status.has_baseline,
        status.has_endline,
        stage_attempt.assessment_stage,
        stage_attempt.assessment_id,
        stage_attempt.assessment_response_id,
        stage_attempt.response_created_at AS assessment_response_created_at,
        stage_attempt.completed_at,
        stage_attempt.final_score,
        stage_attempt.rating,
        baseline.response_created_at AS baseline_response_created_at,
        baseline.completed_at        AS baseline_completed_at,
        baseline.final_score         AS baseline_final_score,
        baseline.rating              AS baseline_rating,
        endline.response_created_at  AS endline_response_created_at,
        endline.completed_at         AS endline_completed_at,
        endline.final_score          AS endline_final_score,
        endline.rating               AS endline_rating
    FROM user_stage_attempts AS stage_attempt
    INNER JOIN user_status AS status
        ON status.user_id = stage_attempt.user_id
    LEFT JOIN first_baseline AS baseline
        ON baseline.user_id = stage_attempt.user_id
    LEFT JOIN first_endline AS endline
        ON endline.user_id = stage_attempt.user_id
    /*PAIRED_ONLY*/
),

/*
Create a consistent question position for every language version.
created_at and id provide deterministic ordering when sort_order is duplicated.
*/
question_catalog AS (
    SELECT
        pg.assessment_id,
        q.language_id,
        q.id AS question_id,
        q.question,
        q.question_type,
        q.option_type,
        q.response_type,
        pg.id AS assessment_page_id,
        pg.page_title,
        pg.sort_order AS page_sort_order,
        q.sort_order AS question_sort_order,
        ROW_NUMBER() OVER (
            PARTITION BY pg.assessment_id, q.language_id
            ORDER BY
                pg.sort_order,
                q.sort_order,
                q.created_at,
                q.id
        ) AS question_position
    FROM quest_rearch_production.ple_assessment_pages AS pg
    INNER JOIN quest_rearch_production.ple_assessment_questions AS q
        ON q.assessment_page_id = pg.id
       AND q.deleted_at IS NULL
    WHERE pg.deleted_at IS NULL
      AND pg.assessment_id IN (
          'bfac6a33-9013-43f5-a085-87fbf1ec95c1',
          '06d3e617-59d6-419d-9a5d-9ac2d9d94498',
          '0dc96821-9489-4558-85e4-88fa8bd84875',
          '6d35233c-6aaa-4bc1-b72a-5b22604235c7',
          '5456ddce-d62f-49cf-b23d-01557a60e8d6',
          '6152b91c-d7b8-4f5c-babe-ddf32e624699'
      )
),

english_questions AS (
    SELECT *
    FROM question_catalog
    WHERE language_id = '9deb9135-9b63-4b49-903e-55668043556c'
),

/*
Remove duplicate answer rows before calculating question scores. The same
radio option can occasionally be stored more than once for one attempt.
*/
deduplicated_response_keys AS (
    SELECT DISTINCT
        rk.assessment_responses_id,
        rk.assessment_question_id,
        rk.assessment_option_id,
        NULLIF(NULLIF(TRIM(rk.response_text), ''), '.') AS response_text
    FROM quest_rearch_production.ple_assessment_response_keys AS rk
    INNER JOIN paired_attempts AS selected_attempt
        ON selected_attempt.assessment_response_id = rk.assessment_responses_id
    WHERE rk.deleted_at IS NULL
)

SELECT
    u.id AS user_id,
    u.name,
    u.email,
    u.mobile,
    u.gender,
    u.created_at AS user_registered_at,
    c.id AS centre_id,
    c.name AS centre_name,
    c.state_id,
    s.name AS state_name,
    c.centre_type_id,
    ct.name AS centre_type_name,
    ct.type AS centre_type_code,
    pa.pathway,
    pa.baseline_pathway,
    pa.endline_pathway,
    pa.attempt_status,
    pa.has_baseline,
    pa.has_endline,
    pa.assessment_stage,
    pa.assessment_id,
    a.title AS assessment_title,
    pa.assessment_response_id,
    pa.assessment_response_created_at,
    pa.completed_at,
    pa.final_score,
    pa.rating,

    response_language.id AS selected_language_id,
    response_language.name AS selected_language,
    source_q.question_position,
    source_q.question_id AS selected_language_question_id,
    english_q.question_id AS english_question_id,
    english_q.question AS question_english,

    GROUP_CONCAT(
        DISTINCT english_o.option
        ORDER BY english_o.sort_order
        SEPARATOR ' | '
    ) AS selected_options_english,

    GROUP_CONCAT(
        DISTINCT NULLIF(NULLIF(TRIM(rk.response_text), ''), '.')
        ORDER BY rk.response_text
        SEPARATOR ' | '
    ) AS free_text_response,

    COALESCE(
        NULLIF(
            GROUP_CONCAT(
                DISTINCT english_o.option
                ORDER BY english_o.sort_order
                SEPARATOR ' | '
            ),
            ''
        ),
        NULLIF(
            GROUP_CONCAT(
                DISTINCT NULLIF(NULLIF(TRIM(rk.response_text), ''), '.')
                ORDER BY rk.response_text
                SEPARATOR ' | '
            ),
            ''
        )
    ) AS response_english,

    CASE
        WHEN COUNT(source_o.id) = 0 THEN NULL
        WHEN MIN(source_o.is_correct_option) = 1 THEN 1
        ELSE 0
    END AS is_correct_option,

    SUM(source_o.score) AS score,

    /* Matches the application's observed whole-number, per-option scoring. */
    SUM(CEIL(source_o.score)) AS rounded_score

FROM paired_attempts AS pa

INNER JOIN quest_rearch_production.users AS u
    ON u.id = pa.user_id
   AND u.deleted_at IS NULL

INNER JOIN quest_rearch_production.centres AS c
    ON c.id = u.centre_id

LEFT JOIN quest_rearch_production.states AS s
    ON c.state_id = s.id COLLATE utf8mb4_unicode_ci

INNER JOIN quest_rearch_production.centre_types AS ct
    ON c.centre_type_id = ct.id COLLATE utf8mb4_unicode_ci
   /*CENTRE_TYPE_FINAL*/

INNER JOIN quest_rearch_production.ple_assessments AS a
    ON a.id = pa.assessment_id
   AND a.deleted_at IS NULL

INNER JOIN deduplicated_response_keys AS rk
    ON rk.assessment_responses_id = pa.assessment_response_id

INNER JOIN question_catalog AS source_q
    ON source_q.question_id = rk.assessment_question_id
   AND source_q.assessment_id = pa.assessment_id

INNER JOIN quest_rearch_production.languages AS response_language
    ON response_language.id = source_q.language_id

INNER JOIN english_questions AS english_q
    ON english_q.assessment_id = source_q.assessment_id
   AND english_q.question_position = source_q.question_position

LEFT JOIN quest_rearch_production.ple_assessment_options AS source_o
    ON source_o.id = rk.assessment_option_id
   AND source_o.assessment_question_id = source_q.question_id
   AND source_o.deleted_at IS NULL

LEFT JOIN quest_rearch_production.ple_assessment_options AS english_o
    ON english_o.assessment_question_id = english_q.question_id
   AND english_o.sort_order = source_o.sort_order
   AND english_o.deleted_at IS NULL

GROUP BY
    u.created_at,
    u.id,
    u.gender,
    c.id,
    c.name,
    c.state_id,
    s.name,
    c.centre_type_id,
    ct.name,
    ct.type,
    pa.user_id,
    u.name,
    u.email,
    u.mobile,
    pa.pathway,
    pa.baseline_pathway,
    pa.endline_pathway,
    pa.attempt_status,
    pa.has_baseline,
    pa.has_endline,
    pa.assessment_stage,
    pa.assessment_id,
    a.title,
    pa.assessment_response_id,
    pa.assessment_response_created_at,
    pa.completed_at,
    pa.final_score,
    pa.rating,
    pa.baseline_response_created_at,
    pa.baseline_completed_at,
    pa.baseline_final_score,
    pa.baseline_rating,
    pa.endline_response_created_at,
    pa.endline_completed_at,
    pa.endline_final_score,
    pa.endline_rating,
    response_language.id,
    response_language.name,
    source_q.question_position,
    source_q.question_id,
    source_q.question,
    english_q.question_id,
    english_q.question,
    source_q.question_type,
    source_q.option_type,
    source_q.response_type

ORDER BY
    pa.pathway,
    pa.user_id,
    CASE pa.assessment_stage
        WHEN 'baseline' THEN 1
        WHEN 'endline' THEN 2
    END,
    source_q.question_position
