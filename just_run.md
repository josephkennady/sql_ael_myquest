python3 run_production_users_by_centre.py \
  --sql-path sql_queries/production_user_one_record_without_career_path.sql \
  --target-table production_users_one_record_without_career_path \
  --centre-sql-path sql_queries/centre_ids.sql \
  --workers 4


python3 run_pipeline.py --workers 6
