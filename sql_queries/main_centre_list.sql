SELECT id FROM (SELECT
    c.id,
    COUNT(DISTINCT u.id) AS user_count
FROM centres c
JOIN users u
    ON u.centre_id = c.id
JOIN centre_project cp
    ON cp.centre_id = c.id
JOIN projects p
    ON p.id = cp.project_id
JOIN programs p2
    ON p.program_id = p2.id
WHERE c.status = 1
    AND c.deleted_at IS NULL
    AND p.status = 1
    AND p.deleted_at IS NULL
    AND p2.id IN (
        'e0a241d3-146f-40a4-9125-f13485478097',
        'a5d01b12-40c8-4434-8b62-8ec9aaedf9b3'
    )
    AND u.status = 1
    AND u.deleted_at IS NULL
    AND u.`type` IN (3, 4)
    AND u.created_at >='2019-01-01'
    AND LOWER(c.name) NOT LIKE '%test%'
    AND LOWER(c.name) NOT LIKE '%demo%'
GROUP BY c.id
ORDER BY user_count DESC) AS a
;