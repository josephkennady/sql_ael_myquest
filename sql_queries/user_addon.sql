SELECT
    u.id AS user_id,
    u.name AS username,
    CASE
        WHEN LOWER(COALESCE(sd.gender, u.gender)) = 'male'   THEN 'Male'
        WHEN LOWER(COALESCE(sd.gender, u.gender)) = 'female' THEN 'Female'
        ELSE 'Other'
    END AS gender,
    c.name AS centre_name,
    o.name AS org_name,
    s.name AS state_name,
    d.name AS district_name,
    t.name AS trade,
    b.name AS batch_name,
    u.is_master_trainer,
    CASE
    WHEN
        (sd.batch_id = b.id
            AND b.deleted_at IS NULL)
    THEN
        1
    ELSE 0
    END AS 'batch_status',
    ct.name AS centre_type,
    c.ple_enabled,
    u.created_platform AS platform,
    ll.first_login
FROM quest_rearch_production.users u

LEFT JOIN (
    SELECT
        user_id,
        MIN(created_at) AS first_login
    FROM quest_rearch_production.login_logs
    GROUP BY user_id
) ll
    ON ll.user_id = u.id

LEFT JOIN quest_rearch_production.student_details sd
    ON sd.user_id = u.id
LEFT JOIN quest_rearch_production.centres c
    ON c.id = u.centre_id
    AND c.deleted_at IS NULL
    AND c.status = 1
LEFT JOIN quest_rearch_production.organisations o
    ON o.id = c.organisation_id
    AND o.status = 1
    AND o.deleted_at IS NULL
LEFT JOIN quest_rearch_production.states s
    ON s.id = c.state_id
LEFT JOIN quest_rearch_production.districts d
    ON d.id = c.district_id
LEFT JOIN quest_rearch_production.trades t
    ON t.id = sd.trade_id
LEFT JOIN quest_rearch_production.batches b
    ON b.id = sd.batch_id
    AND b.deleted_at IS NULL
    AND b.status != 4
LEFT JOIN quest_rearch_production.centre_types ct
    ON ct.id = c.centre_type_id

WHERE
    u.status = 1
    AND u.deleted_at IS NULL
    AND c.deleted_at IS NULL
    AND c.status = 1
    AND o.status = 1
    AND o.deleted_at IS NULL;