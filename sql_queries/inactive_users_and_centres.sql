-- Inactive users: status changed or soft-deleted
SELECT
    id AS user_id
    -- status,
    -- deleted_at
FROM
    users
WHERE
    status != 1
    OR deleted_at IS NOT NULL;

-- Inactive centres: status changed or soft-deleted
SELECT
    id AS centre_id
    -- status,
    -- deleted_at
FROM
    centres
WHERE
    status != 1
    OR deleted_at IS NOT NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- Cleanup: remove inactive records from the analytics snapshot table
-- Run these on the analytics DB (production_users_one_record lives there).
-- The subqueries reference the production source DB (users / centres tables).
-- ─────────────────────────────────────────────────────────────────────────────

-- Remove rows where the user itself is now inactive or deleted
DELETE FROM production_users_one_record
WHERE id IN (
    SELECT id
    FROM users
    WHERE status != 1
       OR deleted_at IS NOT NULL
);

-- Remove rows where the user's centre is now inactive or deleted
DELETE FROM production_users_one_record
WHERE centre_id IN (
    SELECT id
    FROM centres
    WHERE status != 1
       OR deleted_at IS NOT NULL
);