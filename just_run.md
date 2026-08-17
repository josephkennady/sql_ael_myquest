
Step 1:
Run this for full refresh - Joseph Kennady 

python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --replace-existing-users \
  --workers 8

Step 2:

python3 run_user_addon.py --target-table user_addon
python3 run_cleanup_inactive.py --target-table production_users_one_record
python3 run_sql_filters.py --source-table production_users_one_record --target-table sql_ael_filters

Others old note: 
python3 run_production_users_by_centre.py \
  --sql-path sql_queries/production_user_one_record_without_career_path.sql \
  --target-table production_users_one_record_without_career_path \
  --centre-sql-path sql_queries/centre_ids.sql \
  --workers 4


python3 run_pipeline.py --workers 6

