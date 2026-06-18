SELECT 
	f.user_type,
	f.is_ple,
	f.rounded_completion,
	f.gender,
	f.centre_name,
	f.org_name,
	f.state_name,
	f.district_name,
	f.trade,
	f.batch_name,
	f.batch_status,
	f.centre_type,
	f.subject_name,
	f.year_category,
	f.proj_name,
	f.phase,
	f.prog_name
FROM 
(SELECT
-- 	a.user_id AS tlo_users_id,
-- 	a.created_at AS created_at,
-- 	a.user_type AS user_type,
	
CASE
	WHEN user_type = 1 THEN 'Admin'
	WHEN
    user_type = 2
	AND is_master_trainer = 1
THEN
    'Master Trainer'
	WHEN user_type = 2 THEN 'Facilitator'
	WHEN user_type = 3 THEN 'Learner'
	WHEN user_type = 4 THEN 'Alumni'
	ELSE 'Missing Data'
END AS user_type,
	
	-- a.is_ple AS is_ple,
  CASE
      WHEN a.is_ple = 1 THEN 'PLE'
      ELSE 'Non-PLE'
  END AS is_ple,
-- 	a.project_combos,
-- 	a.total_allocated AS a_overa_less_asses_c,
-- 	a.total_assessments_allocated AS a_overa_assess_c,
-- 	a.total_lessons_allocated AS a_overa_lesson_c,
-- 	a.total_completed AS c_overa_less_asses_c,
-- 	a.total_assessments_completed AS c_overa_asse_c,
-- 	a.total_lessons_completed AS c_overa_less_c,
	CAST(ROUND(a.completion_pct) AS UNSIGNED) AS rounded_completion,
	-- a.completion_pct AS rounded_completion,
-- 	b.username AS user_name,
	b.gender,
	b.centre_name,
	b.org_name,
	b.state_name,
	b.district_name,
	b.trade,
	b.batch_name,
	b.batch_status,
	b.centre_type,
-- 	b.platform,
	-- b.ple_enabled,
	CASE
    WHEN b.ple_enabled = 1 THEN 'PLE Centre'
    ELSE 'Non-PLE Centre'
END AS ple_enabled,
-- 	b.first_login,
-- 	a.subject_combos,
-- 	b.is_master_trainer,
	s.*,
	p.*
FROM
	quest_analytics.production_users_one_record_test a
LEFT JOIN quest_analytics.user_addon b ON
	b.user_id = a.user_id

AND JSON_UNQUOTE(
    JSON_EXTRACT(project_combos, '$[0].prog_name')
) IN ('MyQuest', 'Quest Experience Lab')

CROSS JOIN JSON_TABLE(
    a.subject_combos,
    '$[*]'
    COLUMNS (
        subject_id VARCHAR(100) PATH '$.subject_id',
        subject_name VARCHAR(255) PATH '$.subject_name',
        year_category INT PATH '$.year_category',
        avg_score DECIMAL(10,2) PATH '$.avg_score',
        avg_rating DECIMAL(10,2) PATH '$.avg_rating',
        allocated_lessons INT PATH '$.allocated_lessons',
        completed_lessons INT PATH '$.completed_lessons',
        allocated_assessments INT PATH '$.allocated_assessments',
        completed_assessments INT PATH '$.completed_assessments',
        allocated_lessons_and_assessments INT PATH '$.allocated_lessons_and_assessments',
        completed_lessons_and_assessments INT PATH '$.completed_lessons_and_assessments'
    )
) AS s

CROSS JOIN JSON_TABLE(
    a.project_combos,
    '$[*]'
    COLUMNS (
        project_id VARCHAR(100) PATH '$.project_id',
        proj_name VARCHAR(255) PATH '$.proj_name',
        phase VARCHAR(255) PATH '$.phase',
        prog_name VARCHAR(255) PATH '$.prog_name'
    )
) p

WHERE 1=1
-- AND a.user_id IN ('0ee51375-509c-41db-8757-f160dad19a44')
) AS f
GROUP BY 	f.user_type,
	f.is_ple,
	f.rounded_completion,
	f.gender,
	f.centre_name,
	f.org_name,
	f.state_name,
	f.district_name,
	f.trade,
	f.batch_name,
	f.batch_status,
	f.centre_type,
	f.subject_name,
	f.year_category,
	f.proj_name,
	f.phase,
	f.prog_name;
