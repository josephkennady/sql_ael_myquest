SELECT
	a.user_id AS tlo_users_id,
	a.created_at AS created_at,
  a.user_type AS user_type,
CASE
WHEN a.user_type = 1 THEN 'Admin'
WHEN
  a.user_type = 2
      AND is_master_trainer = 1
THEN
  'Master Trainer'
WHEN a.user_type = 2 THEN 'Facilitator'
WHEN a.user_type = 3 THEN 'Learner'
WHEN a.user_type = 4 THEN 'Alumni'
ELSE 'Missing Data'
END AS user_type_e,
CASE
    WHEN b.ple_enabled = 1 THEN 'PLE Centre'
    ELSE 'Non-PLE Centre'
END AS ple_enabled_e,
  CASE
      WHEN a.is_ple = 1 THEN 'PLE'
      ELSE 'Non-PLE'
  END AS is_ple_e,
  a.is_ple,
	a.project_combos,
	a.total_allocated AS a_overa_less_asses_c,
	a.total_assessments_allocated AS a_overa_assess_c,
	a.total_lessons_allocated AS a_overa_lesson_c,
	a.total_completed AS c_overa_less_asses_c,
	a.total_assessments_completed AS c_overa_asse_c,
	a.total_lessons_completed AS c_overa_less_c,
-- 	CAST(a.completion_pct AS UNSIGNED) AS rounded_completion,
ROUND(a.completion_pct) AS rounded_completion,
	-- a.completion_pct AS rounded_completion,
	b.username AS user_name,
	b.gender,
	b.centre_name,
	b.org_name,
	b.state_name,
	b.district_name,
	b.trade,
	b.batch_name,
	b.batch_status,
	b.centre_type,
	b.platform,
  b.ple_enabled,
	b.first_login,
	a.subject_combos,
	b.is_master_trainer
FROM
	quest_analytics.production_users_one_record a
JOIN quest_analytics.user_addon b ON
	b.user_id = a.user_id

AND JSON_UNQUOTE(
    JSON_EXTRACT(project_combos, '$[0].prog_name')
) IN ('MyQuest', 'Quest Experience Lab')

{% set prog_name_filter     = filter_values('prog_name')     | select('string') | list %}
{% set proj_name_filter     = filter_values('proj_name')     | select('string') | list %}
{% set phase_filter         = filter_values('phase')         | select('string') | list %}
{% set sub_name_filter      = filter_values('sub_name')      | select('string') | list %}
{% set year_category_filter = filter_values('year_category') | select('string') | list %}
{% set state_name_filter    = filter_values('state_name')    | select('string') | list %}
{% set district_name_filter = filter_values('district_name') | select('string') | list %}
{% set centre_type_filter   = filter_values('centre_type')   | select('string') | list %}
{% set trade_filter         = filter_values('trade')         | select('string') | list %}
{% set centre_name_filter   = filter_values('centre_name')   | select('string') | list %}
{% set org_name_filter      = filter_values('org_name')      | select('string') | list %}
{% set user_type_filter     = filter_values('user_type')     | select('string') | list %}
{% set gender_filter        = filter_values('gender')        | select('string') | list %}
{% set ple_enabled_filter   = filter_values('ple_enabled')   | select('string') | list %}
{% set is_ple_filter        = filter_values('is_ple')        | select('string') | list %}

-- -------------------------------------------------------
-- Regular column filters
-- -------------------------------------------------------

{% if state_name_filter %}
  AND state_name IN ({{ "'" + "', '".join(state_name_filter) + "'" }})
{% endif %}

{% if district_name_filter %}
  AND district_name IN ({{ "'" + "', '".join(district_name_filter) + "'" }})
{% endif %}

{% if centre_type_filter %}
  AND centre_type IN ({{ "'" + "', '".join(centre_type_filter) + "'" }})
{% endif %}

{% if trade_filter %}
  AND trade IN ({{ "'" + "', '".join(trade_filter) + "'" }})
{% endif %}

{% if centre_name_filter %}
  AND centre_name IN ({{ "'" + "', '".join(centre_name_filter) + "'" }})
{% endif %}

{% if org_name_filter %}
  AND org_name IN ({{ "'" + "', '".join(org_name_filter) + "'" }})
{% endif %}

{% if user_type_filter %}
  AND user_type_e IN ({{ "'" + "', '".join(user_type_filter) + "'" }})
{% endif %}

{% if gender_filter %}
  AND gender IN ({{ "'" + "', '".join(gender_filter) + "'" }})
{% endif %}

{% if ple_enabled_filter %}
  AND ple_enabled_e IN ({{ "'" + "', '".join(ple_enabled_filter) + "'" }})
{% endif %}

{% if is_ple_filter %}
  AND is_ple_e IN ({{ "'" + "', '".join(is_ple_filter) + "'" }})
{% endif %}

-- -------------------------------------------------------
-- project_combos JSON filters (JSON_SEARCH)
-- -------------------------------------------------------

{% if prog_name_filter %}
  AND JSON_VALID(project_combos) = 1
  AND (
    {% for val in prog_name_filter %}
      JSON_SEARCH(project_combos, 'one', '{{ val }}', NULL, '$[*].prog_name') IS NOT NULL
      {% if not loop.last %} OR {% endif %}
    {% endfor %}
  )
{% endif %}

{% if proj_name_filter %}
  AND JSON_VALID(project_combos) = 1
  AND (
    {% for val in proj_name_filter %}
      JSON_SEARCH(project_combos, 'one', '{{ val }}', NULL, '$[*].proj_name') IS NOT NULL
      {% if not loop.last %} OR {% endif %}
    {% endfor %}
  )
{% endif %}

{% if phase_filter %}
  AND JSON_VALID(project_combos) = 1
  AND (
    {% for val in phase_filter %}
      JSON_SEARCH(project_combos, 'one', '{{ val }}', NULL, '$[*].phase') IS NOT NULL
      {% if not loop.last %} OR {% endif %}
    {% endfor %}
  )
{% endif %}

-- -------------------------------------------------------
-- subject_combos JSON filters (JSON_SEARCH)
-- -------------------------------------------------------

{% if sub_name_filter %}
  AND JSON_VALID(subject_combos) = 1
  AND (
    {% for val in sub_name_filter %}
      JSON_SEARCH(subject_combos, 'one', '{{ val }}', NULL, '$[*].sub_name') IS NOT NULL
      {% if not loop.last %} OR {% endif %}
    {% endfor %}
  )
{% endif %}

{% if year_category_filter %}
  AND JSON_VALID(subject_combos) = 1
  AND (
    {% for val in year_category_filter %}
      JSON_SEARCH(subject_combos, 'one', '{{ val }}', NULL, '$[*].year_category') IS NOT NULL
      {% if not loop.last %} OR {% endif %}
    {% endfor %}
  )
{% endif %}