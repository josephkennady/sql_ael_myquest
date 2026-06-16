# AEL MyQuest Pipeline — Data Model & Engineering Reference

Full documentation of the data architecture, every source and analytics table, the allocation engine, JSON column structures, and a data engineering practice guide for this pipeline.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Source Database — Table Reference](#2-source-database--table-reference)
3. [Analytics Database — Table Reference](#3-analytics-database--table-reference)
4. [Data Lineage — End-to-End Flow](#4-data-lineage--end-to-end-flow)
5. [Allocation Engine — How Lessons Are Assigned to Users](#5-allocation-engine--how-lessons-are-assigned-to-users)
6. [JSON Column Reference](#6-json-column-reference)
7. [Superset Consumption Layer](#7-superset-consumption-layer)
8. [What Data Engineers Do Beyond Building Pipelines](#8-what-data-engineers-do-beyond-building-pipelines)
9. [Recommended Next Steps for This Pipeline](#9-recommended-next-steps-for-this-pipeline)

---

## 1. Architecture Overview

### Two-Database Design

The pipeline separates source transactional data from analytics data across two independently hosted MySQL databases.

```
┌─────────────────────────────────────────────────────────────┐
│  SOURCE DB  (quest_rearch_production)                       │
│  MySQL 8 — production LMS                                   │
│  Read-only by the pipeline                                  │
│                                                             │
│  users / centres / subjects / lessons                       │
│  learning_activities / facilitator_learning_activities      │
│  batches / trades / projects / programs / phases            │
│  ple_career_paths / ple_career_path_user                    │
│  login_logs / organisations / states / districts            │
└───────────────────────────┬─────────────────────────────────┘
                            │  SSH tunnel / DB_DIRECT
                            │  Python reads, aggregates
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  ANALYTICS DB  (quest_analytics)                            │
│  MySQL — analytics snapshot                                 │
│  Written by the pipeline                                    │
│                                                             │
│  production_users_one_record   ← main one-row-per-user table│
│  user_addon                    ← supplementary attributes   │
└───────────────────────────┬─────────────────────────────────┘
                            │  Superset virtual dataset SQL
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  SUPERSET DASHBOARD                                         │
│  Youth QApp Phoenix AEL                                     │
│  Jinja2 filter_values() wired to dashboard widgets         │
└─────────────────────────────────────────────────────────────┘
```

### Why Two Databases?

The production LMS database cannot be queried directly for reporting at scale — live joins across activity, allocation, and user tables are slow and risky on a live system. The pipeline:

1. Runs the complex multi-CTE SQL once per centre against the source DB
2. Collapses the result to one row per user with JSON aggregations
3. Writes that row into the analytics DB
4. Superset queries only the fast, pre-aggregated analytics tables

This means report queries hit a small indexed snapshot table (~one row per user) instead of joining millions of activity rows live.

### Connection Modes

| Mode | How it works | When to use |
|---|---|---|
| SSH tunnel | Python opens an SSH tunnel to the bastion, forwards the RDS port locally | Default — server outside the VPC |
| `DB_DIRECT=1` | Python connects to RDS endpoint directly | Server inside the same AWS VPC as RDS |

Both source and destination DBs each have their own tunnel/connection configuration in `.env`.

---

## 2. Source Database — Table Reference

All tables below live in `quest_rearch_production` (the production LMS). The pipeline reads from these tables; it never writes to them.

---

### `users`

**What it is:** The central user registry. Every person who has ever been given access to the LMS is here — learners, facilitators, admins, and master trainers.

**Why the pipeline uses it:** The `active_users` CTE starts here. Only users with `status=1` and no `deleted_at` are included. The `type` column determines which allocation path applies.

**Columns used by the pipeline:**

| Column | Type | Meaning |
|---|---|---|
| `id` | CHAR(36) UUID | Primary key — propagated as `user_id` throughout |
| `name` | VARCHAR | Display name |
| `email` | VARCHAR | Email address |
| `mobile` | VARCHAR | Mobile number |
| `type` | INT | User role: 1=Admin, 2=Facilitator/MT, 3=Learner, 4=Learner variant |
| `is_master_trainer` | INT | 1 if the facilitator is also a master trainer |
| `centre_id` | CHAR(36) | FK → `centres.id` — the centre this user belongs to |
| `project_id` | CHAR(36) | FK → `projects.id` — direct project assignment (secondary; centre→project mapping is primary) |
| `organisation_id` | CHAR(36) | FK → `organisations.id` |
| `is_ple` | INT | NULL or 0 = non-PLE learner; 1 = PLE learner |
| `created_at` | DATETIME | When the account was created — used as the incremental refresh cutoff |
| `status` | INT | 1 = active; anything else = inactive |
| `deleted_at` | DATETIME | Soft-delete timestamp; NULL means not deleted |
| `created_platform` | VARCHAR | Platform the user registered on (app, web, etc.) |
| `gender` | VARCHAR | Gender field on `users` (fallback; `student_details.gender` takes priority) |

**Key filter:** `WHERE u.type IN (1,2,3,4) AND u.status = 1 AND u.deleted_at IS NULL`

---

### `student_details`

**What it is:** Extended learner profile. One row per learner — linked to `users` via `user_id`. Staff users (type 1, 2) may not have a `student_details` row.

**Why the pipeline uses it:** Provides `batch_id`, `trade_id`, gender, educational qualification, placement status, and year-of-admission. All of these affect lesson allocation (batch-subject and trade-subject filtering) and appear in the analytics output.

| Column | Meaning |
|---|---|
| `user_id` | FK → `users.id` |
| `batch_id` | FK → `batches.id` — the batch the learner is enrolled in |
| `trade_id` | FK → `trades.id` — the vocational trade |
| `gender` | Gender (takes priority over `users.gender`) |
| `educational_qualification_id` | FK → qualification lookup |
| `placement_status_id` | FK → placement status lookup |
| `active_year` | Year the learner is currently active in |
| `year_of_admission` | Year the learner was admitted |

---

### `centres`

**What it is:** Training centres. Each centre is the organisational unit that owns users, subjects, and projects.

**Why the pipeline uses it:** The entire pipeline is organised by centre — centre mode iterates over centre IDs. Centre metadata (name, type, location, PLE flag) feeds into `user_addon` and the Superset dashboard.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Centre display name |
| `organisation_id` | FK → `organisations.id` |
| `centre_type_id` | FK → `centre_types.id` |
| `ple_enabled` | Whether PLE mode is active for this centre |
| `status` | 1 = active |
| `deleted_at` | Soft-delete |
| `state_id` | FK → `states.id` |
| `district_id` | FK → `districts.id` |

---

### `organisations`

**What it is:** The organisation that runs one or more centres.

**Why the pipeline uses it:** `user_addon` carries `org_name` to allow Superset filtering by organisation.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Organisation name |
| `status` | 1 = active |
| `deleted_at` | Soft-delete |

---

### `centre_types`, `states`, `districts`

**What they are:** Lookup / dimension tables providing display names for centre types, states, and districts.

**Why the pipeline uses them:** All three feed into `user_addon` as display-name columns for Superset filter widgets. They carry no processing logic — they are pure label lookups.

---

### `subjects`

**What it is:** A curriculum module. Each centre maps to one or more subjects via `centre_subject`. A subject contains one or more lessons.

**Why the pipeline uses it:** Subjects are the primary allocation unit. The `is_ple` flag on each subject determines which allocation path applies.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Subject name |
| `is_ple` | 0 = general (both PLE + non-PLE), 1 = PLE-only, 2 = non-PLE-only |
| `ple_career_path_id` | FK → `ple_career_paths.id` — legacy direct link; superseded by `subject_ple_career_path` |
| `year_to_map` | Year category used to limit subjects by trade duration |
| `status` | 1 = active |
| `deleted_at` | Soft-delete |

**`is_ple` Values — Critical:**

| Value | Meaning | Allocation path |
|---|---|---|
| `0` | General subject | Both PLE and non-PLE users |
| `1` | PLE-specific | PLE users only (`users.is_ple = 1`), requires `subject_ple_career_path` mapping |
| `2` | Non-PLE-specific | Non-PLE users only |

---

### `centre_subject`

**What it is:** The mapping table that says "this centre teaches this subject." Without an entry here, no lessons from a subject will be allocated to any user at that centre.

**Why it matters:** This is the first gate in the allocation chain. If a subject is missing from `centre_subject` for a given centre, no users at that centre will have those lessons — regardless of the user's PLE status or batch.

| Column | Meaning |
|---|---|
| `centre_id` | FK → `centres.id` |
| `subject_id` | FK → `subjects.id` |
| `order` | Display order of the subject at this centre |

---

### `lessons`

**What it is:** An individual learning item inside a subject. Each lesson belongs to exactly one subject.

**Why the pipeline uses it:** Lessons are the atomic unit of allocation and completion. The allocation CTEs join to lessons and filter on `lesson_category_id`, `status`, `deleted_at`, and access flags.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Lesson name |
| `subject_id` | FK → `subjects.id` |
| `lesson_order` | Position within the subject |
| `is_assessment` | 1 if this is an assessment-type lesson |
| `student_access` | 1 if learners (type 3/4) can access this lesson |
| `facilitator_access` | 1 if facilitators can access this lesson |
| `mastertrainer_access` | 1 if master trainers can access this lesson |
| `lesson_category_id` | Category UUID — pipeline filters to `d78bc322-568f-4110-8e24-02ea444d48b7` (learning category only) |
| `lesson_type_id` | FK → `lesson_types.id` |
| `status` | 1 = active |
| `deleted_at` | Soft-delete |

**Important:** `lesson_type` is NOT a direct column on `lessons`. It comes from joining `lesson_types` on `lesson_type_id`.

---

### `lesson_types`

**What it is:** Lookup table for lesson media types.

**Why the pipeline uses it:** The `allocation_filtered` CTE excludes lessons where `lt.name` (after LOWER + TRIM) is `'pdf'`, `'mp4'`, or `'pdf web'`. These are static reference materials, not interactive learning content.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Type name: `video`, `pdf`, `mp4`, `pdf web`, `quiz`, etc. |

---

### `learning_activities`

**What it is:** The learner completion log. One row per learner-lesson attempt (or the best attempt if deduped). Contains `completed` flag, score, rating, and duration.

**Why the pipeline uses it:** The `learner_completion` CTE reads from here for all users with `type IN (3, 4)`. Joined to `allocation_filtered` to mark which allocated lessons have been completed.

| Column | Meaning |
|---|---|
| `user_id` | FK → `users.id` |
| `lesson_id` | FK → `lessons.id` |
| `completed` | 1 = lesson completed |
| `score` | Numeric score (assessments) |
| `rating` | User rating |
| `duration` | Time spent in seconds |

---

### `facilitator_learning_activities`

**What it is:** The staff (facilitator/admin) completion log — same structure as `learning_activities` but for user types 1 and 2.

**Why a separate table exists:** Staff interact with lessons differently from learners (they deliver content, not consume it), so their activity is tracked in a separate table. The pipeline handles both via the `staff_completion` CTE and deduplicates them before merging.

---

### `batches`

**What it is:** A cohort of learners enrolled together, typically representing an academic year or intake group.

**Why the pipeline uses it:** When a learner has a `batch_id`, the pipeline checks `batch_subject` to verify the subject is mapped to that batch before allocating it. This prevents learners from seeing curriculum outside their batch's scope.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Batch name |
| `status` | Not 4 = active (status 4 = archived or similar) |
| `deleted_at` | Soft-delete |

---

### `batch_subject`

**What it is:** Maps which subjects are available to a specific batch.

**Why it matters:** An additional allocation gate after `centre_subject`. If a learner has a `batch_id` and the subject is NOT in `batch_subject` for that batch, the subject is excluded from their allocation — even if it is in `centre_subject`.

| Column | Meaning |
|---|---|
| `batch_id` | FK → `batches.id` |
| `subject_id` | FK → `subjects.id` |

---

### `trades`

**What it is:** Vocational trade taxonomy. Each learner can be enrolled in a trade (e.g. Electrical, Plumbing, IT) via `student_details.trade_id`.

**Why the pipeline uses it:** When a learner has a `trade_id`, the pipeline checks `subject_trade` and filters by `year_to_map` (subject year category) against `trades.duration` (length of the trade in years). This prevents learners from seeing Year 2 subject content in their first year of a 2-year trade.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Trade name |
| `duration` | Trade duration in years — used for year-category filtering |

---

### `subject_trade`

**What it is:** Maps which subjects are available to a specific trade.

**Why it matters:** Like `batch_subject`, this is an additional allocation gate. If a learner has a `trade_id` and the subject is NOT mapped to that trade, it is excluded from their allocation.

---

### `projects` and `programs`

**What they are:**
- `programs` — top-level programme umbrella (e.g. "MyQuest", "Quest Experience Lab")
- `projects` — a specific project under a programme. Multiple projects can belong to one programme. Each project belongs to one programme via `program_id`.

**Why the pipeline uses them:** The `project_combos` JSON array is built from these. Each entry in `project_combos` tells the dashboard which programme and project a user is associated with — enabling filtering by programme or project name.

| Table | Key columns |
|---|---|
| `programs` | `id`, `name`, `status`, `deleted_at` |
| `projects` | `id`, `name`, `program_id`, `status`, `deleted_at` |

---

### `centre_project`

**What it is:** Maps which projects a centre is enrolled in. A centre can be in multiple projects.

**Why it matters:** The pipeline builds `project_combos` by walking `centre → centre_project → projects → programs`. Without an entry in `centre_project`, a centre's users will have no `project_combos` data.

---

### `phases`

**What it is:** A phase is a milestone stage within a project (e.g. "Phase 1", "Induction"). Phases belong to projects via `phase_project`.

**Why the pipeline uses it:** Phase name is included in each element of the `project_combos` JSON array, allowing Superset to filter users by which project phase they are currently in.

---

### `centre_phase`, `phase_project`, `batch_phase`, `phase_users`

**What they are:** The phase-assignment mapping tables:

| Table | What it maps |
|---|---|
| `centre_phase` | Which phases are available at which centre |
| `phase_project` | Which project each phase belongs to |
| `batch_phase` | Which phases a batch is assigned to |
| `phase_users` | Direct user-to-phase assignments (used when not going via batch) |

**Why the pipeline uses them:** Phase is resolved via two separate paths in `main_phases`:
1. **Batch path:** User has a `batch_id` → look up `batch_phase` → `centre_phase` → `phases`
2. **Direct path:** User is directly assigned to a phase in `phase_users` → `centre_phase` → `phases`

Both paths are unioned before being joined to the final project/phase output.

---

### `ple_career_paths`

**What it is:** The set of career pathway options available to PLE (Practical Life Education) learners. Examples: "I want to work in a Company", "I want to start a business", "I want to work as a freelancer".

**Why the pipeline uses it:** PLE learners must select a career path. The pipeline joins via `ple_career_path_user.job_type_id` → `ple_career_paths.id` to find the learner's selected path. The path name is included in the analytics output and in `project_combos`.

| Column | Meaning |
|---|---|
| `id` | PK UUID |
| `name` | Career path display name |
| `deleted_at` | Soft-delete |

---

### `ple_career_path_user`

**What it is:** Records which career path each PLE user has selected. A user may change career paths over time — the pipeline picks the most recent active record via `ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY updated_at DESC)`.

**Critical schema note:** The FK to `ple_career_paths` is called `job_type_id`, NOT `career_path_id` or `ple_career_path_id`.

| Column | Meaning |
|---|---|
| `user_id` | FK → `users.id` |
| `job_type_id` | FK → `ple_career_paths.id` — the selected career path |
| `status` | 1 = active selection |
| `deleted_at` | Soft-delete |
| `updated_at` | When the selection was last updated — used to pick the latest |

---

### `subject_ple_career_path`

**What it is:** Maps which subjects are relevant to which PLE career paths. For subjects with `is_ple=1`, this mapping must exist for the subject to be allocated to a PLE learner on a given career path.

**Why it matters:** A PLE learner is allocated a subject only if:
- The subject has `is_ple=0` (general — no mapping needed), OR
- The subject has `is_ple=1` AND an entry exists in `subject_ple_career_path` linking that subject to the learner's career path

Missing entries here are the most common reason PLE learners show zero completions — the subjects exist and are mapped to the centre but are not mapped to any career path.

| Column | Meaning |
|---|---|
| `ple_career_path_id` | FK → `ple_career_paths.id` |
| `subject_id` | FK → `subjects.id` |

---

### `login_logs`

**What it is:** A log of every user login event with timestamp.

**Why the pipeline uses it:** `user_addon` derives `first_login` by selecting `MIN(created_at)` per `user_id` from this table. This tells the dashboard when a user first accessed the platform.

| Column | Meaning |
|---|---|
| `user_id` | FK → `users.id` |
| `created_at` | Login timestamp |

---

## 3. Analytics Database — Table Reference

These tables live in `quest_analytics` (the analytics/destination DB). They are written by the pipeline and read by Superset.

---

### `production_users_one_record`

**What it is:** The primary analytics table. One row per active user. Produced by running `production_user_one_record_subject_project_combo.sql` once per centre via the pipeline.

**Why one row per user:** Superset metrics (completion rate, total completions, etc.) must be computed at the user grain without double-counting. Storing one row per user with aggregated JSON columns for multi-valued dimensions (subjects, projects) avoids the fan-out problem that comes from storing one row per user-subject or user-lesson.

**Grain:** One row per `user_id`.

**How it is written:**
- Full refresh: table is DROPped and reCREATEd, then all centres are processed in sequence/parallel.
- Incremental refresh: rows for changed users are DELETEd then re-INSERTed.

**Columns:**

| Column | Type | Source CTE | Meaning |
|---|---|---|---|
| `user_id` | CHAR(36) | `one_user_row` ← `active_users` ← `users` | User's UUID primary key |
| `user_type` | INT | `users.type` | 1=Admin, 2=Facilitator/MT, 3=Learner, 4=Learner variant |
| `centre_id` | CHAR(36) | `users.centre_id` | Centre the user belongs to |
| `organisation_id` | CHAR(36) | `users.organisation_id` | Organisation FK |
| `is_ple` | INT | `users.is_ple` | NULL/0=non-PLE, 1=PLE learner |
| `created_at` | DATETIME | `users.created_at` | Account creation date — used as incremental refresh cutoff |
| `batch_id` | CHAR(36) | `student_details.batch_id` | Enrolled batch |
| `trade_id` | CHAR(36) | `student_details.trade_id` | Enrolled trade |
| `total_allocated` | INT | `user_summary` | Total lessons + assessments allocated |
| `total_lessons_allocated` | INT | `user_summary` | Lessons allocated (excluding assessments) |
| `total_assessments_allocated` | INT | `user_summary` | Assessments allocated |
| `total_completed` | INT | `user_summary` | Total lessons + assessments completed |
| `total_lessons_completed` | INT | `user_summary` | Lessons completed |
| `total_assessments_completed` | INT | `user_summary` | Assessments completed |
| `completion_pct` | DECIMAL(5,2) | `user_summary` | `total_completed / total_allocated * 100`, NULL-safe |
| `project_combos` | JSON | `user_project_combos` | Array of programme/project/phase objects — see §6 |
| `subject_combos` | JSON | `user_subject_combos` | Array of subject-level completion summaries — see §6; NULL if zero completions |

**Freshness:** Updated every time the pipeline runs. For incremental runs, only changed users are refreshed. The `created_at` column is the watermark used by the incremental detector.

**Cleanup:** `run_cleanup_inactive.py` DELETEs rows where `user_id` or `centre_id` matches an inactive/deleted record in the source DB.

---

### `user_addon`

**What it is:** Supplementary user attribute table. One row per active user. Written by `run_user_addon.py` running `user_addon.sql`. Joined to `production_users_one_record` in the Superset virtual dataset.

**Why a separate table:** These attributes are stable demographic data (gender, location, centre name, first login). Storing them separately from the main pipeline table means they can be refreshed independently — a full rebuild of `user_addon` doesn't require rerunning the heavy per-centre allocation SQL.

**Grain:** One row per `user_id`.

**How it is written:** Full replace on every run (DROP + CREATE + INSERT). Runs as Step 2 in `run_pipeline.py`.

**Columns:**

| Column | Source | Meaning |
|---|---|---|
| `user_id` | `users.id` | FK → `production_users_one_record.user_id` |
| `username` | `users.name` | Display name |
| `gender` | `COALESCE(student_details.gender, users.gender)` → normalised | `Male`, `Female`, or `Other` |
| `centre_name` | `centres.name` | Centre display name |
| `org_name` | `organisations.name` | Organisation name |
| `state_name` | `states.name` | State |
| `district_name` | `districts.name` | District |
| `trade` | `trades.name` | Vocational trade name |
| `batch_name` | `batches.name` | Batch name |
| `is_master_trainer` | `users.is_master_trainer` | 1 if master trainer |
| `batch_status` | Computed | 1 if user has a batch linked to a non-deleted, non-status-4 batch; 0 otherwise |
| `centre_type` | `centre_types.name` | Centre type label |
| `ple_enabled` | `centres.ple_enabled` | Whether centre has PLE mode active |
| `platform` | `users.created_platform` | Registration platform |
| `first_login` | `MIN(login_logs.created_at)` | Earliest login timestamp |

---

## 4. Data Lineage — End-to-End Flow

This diagram traces data from raw source tables to the final Superset dashboard column.

```
SOURCE DB (quest_rearch_production)
│
├── users ──────────────────────────────────────────────────────────┐
│   └── student_details (LEFT JOIN on user_id)                     │
│                                                                   ▼
│                                                          active_users CTE
│                                                          (filtered: status=1,
│                                                           not deleted,
│                                                           type IN 1,2,3,4)
│
├── centre_subject ─────────────────────────────────────────────────┐
├── subjects ──────────────────────────────────────────────────────┤
├── lessons ──────────────────────────────────────────────────────┤
├── lesson_types ────────────────────────────────────────────────┤
├── batch_subject (optional, when user has batch_id) ────────────┤
├── subject_trade (optional, when user has trade_id) ────────────┤
├── trades (for year_to_map filtering) ─────────────────────────┤
│                                                                  ▼
│                                               non_ple_allocation CTE
│                                               (type 3/4, is_ple != 1,
│                                                subjects is_ple IN 0,2)
│
├── ple_career_path_user (latest via ROW_NUMBER) ────────────────┐
├── ple_career_paths ────────────────────────────────────────────┤
├── subject_ple_career_path ─────────────────────────────────────┤
├── centre_subject ─────────────────────────────────────────────┤
├── subjects / lessons ─────────────────────────────────────────┤
│                                                                 ▼
│                                               ple_allocation CTE
│                                               (type 3/4, is_ple=1,
│                                                subjects is_ple IN 0,1,
│                                                career path required)
│
├── centre_subject / subjects / lessons ─────────────────────────┐
│                                                                 ▼
│                                               staff_allocation CTE
│                                               (type 1/2, all subjects)
│
│   [all three paths UNION ALL'd → allocation_union]
│   [ROW_NUMBER dedup per user+lesson → allocation_dedup]
│   [filter out pdf/mp4/pdf web → allocation_filtered]
│
├── learning_activities (learners) ────────────────────────────┐
├── facilitator_learning_activities (staff) ─────────────────┤
│                                                              ▼
│                                            learner_completion / staff_completion CTEs
│                                            (GROUP BY user_id, lesson_id)
│                                            [UNION + ROW_NUMBER dedup → completion_dedup]
│
│   [allocation_filtered LEFT JOIN completion_dedup → merged]
│
│   [user_summary: COUNT/SUM per user_id]
│   [subject_output: aggregate per user+subject]
│   [user_subject_combos: JSON_ARRAYAGG per user → subject_combos]
│
├── centre_project ─────────────────────────────────────────────┐
├── projects / programs ──────────────────────────────────────┤
├── phases / centre_phase / phase_project ──────────────────┤
├── batch_phase / phase_users ────────────────────────────────┤
│                                                              ▼
│                                            user_project_phase_rows
│                                            [JSON_ARRAYAGG → project_combos]
│
│   [one_user_row + user_project_combos + user_subject_combos → final SELECT]
│
▼
ANALYTICS DB (quest_analytics)
└── production_users_one_record  [one row per user]
    (written by run_production_users_by_centre.py)

SOURCE DB (quest_rearch_production)
├── users / student_details / centres / organisations
├── states / districts / trades / batches / centre_types
└── login_logs
    ▼
ANALYTICS DB (quest_analytics)
└── user_addon  [one row per user]
    (written by run_user_addon.py)

ANALYTICS DB
└── production_users_one_record
    LEFT JOIN user_addon ON user_id
    + Jinja2 filter_values() clauses
    ▼
SUPERSET virtual dataset → Youth QApp Phoenix AEL dashboard
```

---

## 5. Allocation Engine — How Lessons Are Assigned to Users

The allocation engine is the core of this pipeline. It determines, for every active user, which lessons they are expected to complete. This drives the `total_allocated` counts and the `completion_pct` in the analytics table.

### Three Allocation Paths

Every active user goes through exactly one of three allocation CTEs, determined by their `user_type` and `is_ple` flag:

```
Active user
│
├── user_type IN (3, 4) AND is_ple != 1  ──→  non_ple_allocation
│
├── user_type IN (3, 4) AND is_ple = 1   ──→  ple_allocation
│
└── user_type IN (1, 2)                  ──→  staff_allocation
```

### Non-PLE Allocation

**Who:** Learners who are not in PLE mode (`users.is_ple` IS NULL or != 1).

**What subjects:** `subjects.is_ple IN (0, 2)` — general subjects and non-PLE-specific subjects.

**Allocation gates (all must pass):**
1. Subject is in `centre_subject` for the user's centre
2. If user has a `batch_id` → subject must be in `batch_subject` for that batch
3. If user has a `trade_id` → subject must be in `subject_trade` for that trade
4. Subject `year_to_map` must be ≤ `trades.duration` (or `year_to_map` is NULL/0)
5. Lesson `lesson_category_id` = `d78bc322-...` (the learning category)
6. Lesson `student_access = 1`

### PLE Allocation

**Who:** Learners with `users.is_ple = 1`.

**What subjects:** `subjects.is_ple IN (0, 1)` — general subjects and PLE-specific subjects.

**Allocation gates (all must pass):**
1. User has an active `ple_career_path_user` record (the most recent one via `ROW_NUMBER`)
2. Subject is in `centre_subject` for the user's centre
3. If `subject.is_ple = 1` → subject must be in `subject_ple_career_path` for the user's career path
4. If `subject.is_ple = 0` → no career path mapping required (general subject)
5. If user has a `batch_id` → subject must be in `batch_subject` for that batch
6. `year_to_map` check (same as non-PLE)
7. Lesson category and `student_access` filters (same as non-PLE)

**The key rule:** `(s.is_ple = 0 OR spcp.subject_id IS NOT NULL)` — this is what allows `is_ple=0` subjects to be allocated without a `subject_ple_career_path` entry.

### Staff Allocation

**Who:** Admins (type 1) and Facilitators/Master Trainers (type 2).

**What subjects:** All subjects with `is_ple IN (0, 1, 2)` — no PLE/non-PLE filtering for staff.

**Lesson access by staff role:**
| Staff role | Access rule |
|---|---|
| Admin (type 1) | All lessons in the subject |
| Facilitator (type 2, not MT) | Only lessons with `facilitator_access = 1` |
| Master Trainer (type 2, `is_master_trainer = 1`) | Only lessons with `mastertrainer_access = 1` |

### Deduplication

After the three paths are unioned, a `ROW_NUMBER()` window deduplicates the combined set. If a lesson appears in both PLE and non-PLE paths for the same user (possible for `is_ple=0` subjects), the most recent career path update wins. Ties break by `lesson_order ASC`.

### Post-Allocation Filter

After dedup, `allocation_filtered` removes lessons where the lesson type is `pdf`, `mp4`, or `pdf web`. These are reference documents — not interactive learning content. Only interactive lessons count toward `total_allocated` and `completion_pct`.

### Users with Zero Completions

If a user has zero completions across all their allocated lessons, the SQL still produces a row for them. The `zero_completion_subject_rows` CTE generates one placeholder row per subject (with NULL lesson fields) so the user appears in the dashboard with `completion_pct = 0` rather than being absent entirely.

### Users with No Allocation

If a user passes none of the allocation gates (no subjects mapped to their centre, no career path for PLE user, etc.), `no_allocation_user_rows` captures them with `total_allocated = 0`. These users appear in the dashboard and are important for identifying setup problems.

---

## 6. JSON Column Reference

The final SELECT produces two JSON columns that encode multi-valued relationships without needing a separate row per item.

---

### `project_combos`

**Type:** JSON array of objects, or NULL if the user's centre has no project mappings.

**One element per unique programme/project/phase combination the user belongs to.**

**Structure:**
```json
[
  {
    "prog_name":  "MyQuest",
    "project_id": "550e8400-e29b-41d4-a716-446655440000",
    "proj_name":  "MyQuest Phase 2 - State ABC",
    "p_phase_id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
    "phase":      "Phase 1"
  },
  {
    "prog_name":  "MyQuest",
    "project_id": "550e8400-e29b-41d4-a716-446655440000",
    "proj_name":  "MyQuest Phase 2 - State ABC",
    "p_phase_id": null,
    "phase":      null
  }
]
```

**How it is built:**
1. `active_centres` + `centre_project_map` + `active_projects` + `active_programs` → `main_centre_project`
2. Batch path: `active_users → batch → batch_phase → centre_phase → phases → phase_project`
3. Direct path: `phase_users → active_users → centre_phase → phases → phase_project`
4. `main_phases` = UNION of both paths
5. `user_project_phase_rows` = distinct combinations per user
6. `JSON_ARRAYAGG(JSON_OBJECT(...))` collapses to one row per user

**When `phase` is NULL:** The user's centre is in the project but has no batch-phase or direct-phase assignment.

**Superset filter usage:**
```sql
JSON_SEARCH(project_combos, 'one', 'MyQuest', NULL, '$[*].prog_name') IS NOT NULL
```

---

### `subject_combos`

**Type:** JSON array of objects, or **NULL if the user has zero total completions.**

**One element per subject the user has partial or full completion in.**

**Structure:**
```json
[
  {
    "subject_id":                         "a1b2c3d4-...",
    "subject_name":                       "Digital Literacy",
    "avg_score":                          82.5,
    "avg_rating":                         4.2,
    "completed_lessons_and_assessments":  8,
    "allocated_lessons_and_assessments":  10,
    "allocated_assessments":              2,
    "allocated_lessons":                  8,
    "completed_assessments":              1,
    "completed_lessons":                  7,
    "year_category":                      1
  }
]
```

**Important:** `subject_combos` is NULL for users with zero total completions. This is intentional — the CTE condition is `WHEN MAX(total_lessons_completed) > 0 THEN JSON_ARRAYAGG(...)`. This prevents empty JSON arrays from cluttering the dashboard and keeps NULL as the canonical "no progress" signal.

**`year_category`** corresponds to `subjects.year_to_map` — the year level of the subject (1 = Year 1, 2 = Year 2, etc.).

**Superset filter usage:**
```sql
JSON_SEARCH(subject_combos, 'one', 'Digital Literacy', NULL, '$[*].subject_name') IS NOT NULL
```

---

## 7. Superset Consumption Layer

The Superset virtual dataset (`sql_queries/superset_sql_jinja_file.sql`) joins the two analytics tables and exposes all columns for dashboard filtering.

### Join

```sql
FROM quest_analytics.production_users_one_record a
LEFT JOIN quest_analytics.user_addon b ON b.user_id = a.user_id
```

The LEFT JOIN means a user present in `production_users_one_record` but missing from `user_addon` still appears in the dashboard — with NULL demographic columns. This can happen if `run_user_addon.py` is run before `run_production_users_by_centre.py` on a first run, or if the addon step failed.

### Programme Filter (Hardcoded)

```sql
AND JSON_UNQUOTE(JSON_EXTRACT(project_combos, '$[0].prog_name')) IN ('MyQuest', 'Quest Experience Lab')
```

This filters the dataset to users whose first project element is MyQuest or Quest Experience Lab. This is a hardcoded baseline scope — it is NOT a Jinja2 filter widget. Adjust this line if the programme scope changes.

### Jinja2 Filter Pattern

There are three types of filter patterns in the virtual dataset:

**1. Plain string column filter** (state, district, centre, trade, etc.):
```sql
{% set state_name_filter = filter_values('state_name') | select('string') | list %}
{% if state_name_filter %}
  AND state_name IN ({{ "'" + "', '".join(state_name_filter) + "'" }})
{% endif %}
```

**2. JSON array filter** (`project_combos`, `subject_combos` fields):
```sql
{% if prog_name_filter %}
  AND JSON_VALID(project_combos) = 1
  AND (
    {% for val in prog_name_filter %}
      JSON_SEARCH(project_combos, 'one', '{{ val }}', NULL, '$[*].prog_name') IS NOT NULL
      {% if not loop.last %} OR {% endif %}
    {% endfor %}
  )
{% endif %}
```

**3. Numeric filter** (`rounded_completion`):
```sql
{% set rounded_completion_filter = filter_values('rounded_completion') | map('int') | list %}
{% if rounded_completion_filter %}
  AND ROUND(a.completion_pct) IN ({{ rounded_completion_filter | join(', ') }})
{% endif %}
```

### Complete Filter Widget Inventory

| Jinja2 variable | Column filtered | Type |
|---|---|---|
| `prog_name_filter` | `project_combos[*].prog_name` | JSON search |
| `proj_name_filter` | `project_combos[*].proj_name` | JSON search |
| `phase_filter` | `project_combos[*].phase` | JSON search |
| `sub_name_filter` | `subject_combos[*].sub_name` | JSON search |
| `year_category_filter` | `subject_combos[*].year_category` | JSON search |
| `state_name_filter` | `state_name` | String IN |
| `district_name_filter` | `district_name` | String IN |
| `centre_type_filter` | `centre_type` | String IN |
| `trade_filter` | `trade` | String IN |
| `centre_name_filter` | `centre_name` | String IN |
| `org_name_filter` | `org_name` | String IN |
| `user_type_filter` | `user_type` | String IN |
| `gender_filter` | `gender` | String IN |
| `batch_name_filter` | `batch_name` | String IN |
| `batch_status_filter` | `batch_status` | String IN |
| `rounded_completion_filter` | `ROUND(completion_pct)` | Numeric IN |
| `ple_enabled_filter` | `ple_enabled` | String IN |
| `is_ple_filter` | `is_ple` | String IN |

---

## 8. What Data Engineers Do Beyond Building Pipelines

Building a pipeline is the foundation. Professional data engineering practice involves several additional disciplines that protect data quality, enable trust, and make the system maintainable over years — not just months.

---

### Data Quality Checks

Data quality checks are assertions about the data that should always be true. They run after each pipeline step and fail loudly if violated.

**Row count assertions:** After writing `production_users_one_record`, assert:
- Total row count is not zero
- Row count is not dramatically lower than the previous run (e.g. > 20% drop is suspicious)
- No `user_id` appears more than once (uniqueness constraint)

**Null checks on critical columns:** `user_id`, `centre_id`, `user_type`, `created_at`, `total_allocated` should never be NULL.

**Range checks:**
- `completion_pct` must be between 0.00 and 100.00
- `total_completed` must be ≤ `total_allocated`
- `total_lessons_completed` + `total_assessments_completed` must equal `total_completed`

**Referential integrity checks:**
- Every `centre_id` in `production_users_one_record` should exist in the source `centres` table
- Every `user_id` in `user_addon` should also exist in `production_users_one_record`

**Centre-level regression check:** For each centre that ran today, compare row count to the previous run. A centre that had 200 users yesterday and has 0 today is a pipeline problem, not real data.

Tools that implement these natively: **Great Expectations**, **dbt tests**, **Soda Core**.

---

### Data Observability

Observability means you can answer "is this data fresh, complete, and correct?" without running a query yourself.

**Freshness tracking:** Add a `pipeline_run_at DATETIME` column to `production_users_one_record`. Set it to the pipeline start timestamp on every write. A Superset alert can fire if `MAX(pipeline_run_at) < NOW() - INTERVAL 2 DAY`.

**Volume tracking:** Log the row count written per centre per run to a `pipeline_runs` audit table:
```sql
CREATE TABLE pipeline_runs (
  run_id       CHAR(36),
  run_at       DATETIME,
  target_table VARCHAR(255),
  centre_id    CHAR(36),
  rows_written INT,
  duration_ms  INT
);
```

**Schema drift detection:** If the source DB adds a new column or changes a data type, the pipeline's pandas `_create_table_sql` will silently use the wrong MySQL type. A pre-run schema fingerprint check (hash the column names and types) catches this before any data is written.

**Alerting:** The existing email report is a baseline. Future-state: send a Slack/webhook alert when any step fails, with a link to the log file.

---

### Data Lineage Tracking

Data lineage documents where every column in the analytics tables came from — which source table, which transformation, which business rule.

**Column lineage for `production_users_one_record`:**

| Analytics Column | Source Table.Column | Transformation |
|---|---|---|
| `user_id` | `users.id` | Direct — no transformation |
| `user_type` | `users.type` | Direct |
| `centre_id` | `users.centre_id` | Direct |
| `organisation_id` | `users.organisation_id` | Direct |
| `is_ple` | `users.is_ple` | Direct |
| `created_at` | `users.created_at` | Direct — also the incremental watermark |
| `batch_id` | `student_details.batch_id` | JOIN — null if no student_details row |
| `trade_id` | `student_details.trade_id` | JOIN — null if no student_details row |
| `total_allocated` | `lessons.id` COUNT | Count of allocated non-filtered lessons per user |
| `total_lessons_allocated` | `lessons.is_assessment=0` COUNT | Subset where is_assessment=0 |
| `total_assessments_allocated` | `lessons.is_assessment=1` COUNT | Subset where is_assessment=1 |
| `total_completed` | `learning_activities.completed=1` SUM | Joined against allocation |
| `completion_pct` | Derived | `total_completed / total_allocated * 100` |
| `project_combos` | `projects`, `programs`, `phases`, `centre_project`, `centre_phase` | JSON_ARRAYAGG aggregation |
| `subject_combos` | `subjects`, `learning_activities` | JSON_ARRAYAGG aggregation — NULL if zero completions |

Tools that automate lineage: **dbt** (lineage graph built from `ref()` calls), **Apache Atlas**, **OpenLineage / Marquez**.

---

### Data Testing

Data testing is the practice of running automated assertions before or after a transformation, similar to unit tests in software.

**What to test for this pipeline:**

| Test | Type | Assertion |
|---|---|---|
| `production_users_one_record` uniqueness | Schema test | No duplicate `user_id` values |
| `completion_pct` range | Value test | `completion_pct BETWEEN 0 AND 100` |
| Completed ≤ Allocated | Consistency test | `total_completed <= total_allocated` for every row |
| `subject_combos` is NULL when completions=0 | Business rule test | `WHERE total_completed = 0` → `subject_combos IS NULL` |
| `project_combos` valid JSON | Format test | `JSON_VALID(project_combos) = 1 OR project_combos IS NULL` |
| `user_addon` covers all users | Coverage test | Count of `user_addon` ≈ count of `production_users_one_record` |
| No active user missing from output | Completeness test | Source active user count ≈ analytics row count |

---

### Schema Evolution

The source LMS database schema changes over time. The pipeline must handle this gracefully.

**Risk:** If a source column is renamed or dropped, the pipeline crashes or produces silently wrong data.

**Practices:**
- Pin exact column names in all SQL (already done — `users.id`, `sd.batch_id`, etc. are all explicit)
- Version the main SQL file (currently handled via git)
- When the source schema changes, run `debug_centre.py` against a test centre to confirm the allocation chain still works end-to-end before deploying to production
- Add a `DESCRIBE users` check to the pipeline startup that hashes the column list and alerts if it changes

**Schema migration log** — track any source schema changes that required SQL updates:

| Date | Change | SQL impact |
|---|---|---|
| 2026-06 | `ple_career_path_user.job_type_id` confirmed as FK (not `career_path_id`) | Updated `debug_centre.py` and documented in `dev_notes.md` |
| 2026-06 | `lesson_type` not a column on `lessons` — must join `lesson_types` | Updated `debug_centre.py` |
| 2026-06 | `batch_id` on `student_details` not `users` | Updated `debug_centre.py` |

---

### Backfill Strategies

A backfill is a re-run of the pipeline over historical or previously failed data.

**Full centre backfill** — use when the table needs to be rebuilt from scratch:
```bash
python3 run_pipeline.py --target-table production_users_one_record --workers 8
```
(Table missing → auto-detected → full centre refresh via `centre_ids.sql`)

**Partial backfill** — use when some centres failed or were skipped:
```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/centre_ids.sql \
  --target-table production_users_one_record \
  --skip-existing --workers 8
```

**Single-centre backfill** — use when one centre needs refreshing:
```bash
python3 run_production_users_by_centre.py \
  --centre-sql-path sql_queries/my_one_centre.sql \
  --target-table production_users_one_record \
  --workers 1
```
Where `my_one_centre.sql` contains `SELECT 'your-centre-uuid-here'`.

**User-level backfill** — use when specific users had data corrections applied upstream:
```bash
python3 run_production_users_by_centre.py \
  --user-sql-path sql_queries/user_ids.sql \
  --target-table production_users_one_record \
  --replace-existing-users
```

---

### Partitioning and Indexing

The analytics table is currently unpartitioned and unindexed (beyond the implicit primary key). As the user base grows, query performance will degrade.

**Recommended indexes for `production_users_one_record`:**

```sql
-- Superset dashboard filters primarily by centre_id
ALTER TABLE production_users_one_record ADD INDEX idx_centre (centre_id);

-- User type and is_ple are used in nearly every dashboard query
ALTER TABLE production_users_one_record ADD INDEX idx_user_type (user_type);
ALTER TABLE production_users_one_record ADD INDEX idx_is_ple (is_ple);

-- Incremental refresh reads MAX(created_at) on every run
ALTER TABLE production_users_one_record ADD INDEX idx_created_at (created_at);

-- Cleanup deletes by user_id and centre_id
ALTER TABLE production_users_one_record ADD INDEX idx_user_id (user_id);
```

**Note:** The current `replace` mode (DROP + CREATE) destroys all indexes on every full rebuild. Consider switching to a TRUNCATE + INSERT pattern for full rebuilds, which preserves index definitions.

---

### Data Governance and PII

The analytics tables contain personally identifiable information (PII):

| Column | PII level | Location |
|---|---|---|
| `user_id` | Pseudonymous (UUID) | `production_users_one_record` |
| `username` | Direct PII — real name | `user_addon` |
| `gender` | Sensitive demographic | `user_addon` |
| `email`, `mobile` | Commented out in final SELECT | Main SQL (currently excluded from output) |
| `first_login` | Behavioural | `user_addon` |

**Current posture:** `email` and `mobile` are explicitly commented out in the final SELECT of the main SQL. This is intentional and should not be uncommented unless there is a clear, documented need.

**Recommended practices:**
- Document who has SELECT access to `quest_analytics`
- Ensure `user_addon` is not exposed in any public-facing Superset dashboard (use row-level security in Superset)
- Apply a data retention policy: users who have been inactive for > N years should be excluded from analytics (currently cleanup only removes fully deleted/inactive users)
- Audit all `.env` files and `DB_Config/` key files — confirm they are never committed to Git (enforced via `.gitignore`)

---

### SLAs and Pipeline Health

A Service Level Agreement (SLA) for a pipeline defines the expected freshness of the data and what action to take when it is missed.

**Suggested SLA for this pipeline:**

| Metric | Target | Alert threshold |
|---|---|---|
| Pipeline completes | By 04:00 daily | Alert if not completed by 05:00 |
| Data freshness | `MAX(pipeline_run_at) < 26 hours ago` | Alert on Superset/email |
| Step failure rate | 0 failed steps | Email already configured |
| Row count drop | < 5% day-over-day per centre | Log warning if > 20% |

---

### Documentation as Code

The `dev_notes.md`, `README.md`, and `data_model.md` files in this repository are the documentation layer. They should be updated whenever:
- A source schema change requires a SQL fix
- A new allocation rule is added
- A new analytics column is added to either output table
- A bug is found and fixed (add to dev_notes §Errors and Fixes)
- A new pipeline step is added to `run_pipeline.py`

---

## 9. Recommended Next Steps for This Pipeline

Prioritised by impact and effort:

| Priority | Action | Effort | Impact |
|---|---|---|---|
| High | Add `pipeline_run_at` column to `production_users_one_record` | Low | Freshness monitoring in Superset |
| High | Add `pipeline_runs` audit table (rows written per centre per run) | Medium | Observability, regression detection |
| High | Add post-write row count check to `run_pipeline.py` — fail if 0 rows | Low | Catches silent write failures |
| High | Add database indexes on `centre_id`, `user_type`, `is_ple`, `created_at` | Low | Query performance at scale |
| Medium | Add uniqueness assertion on `user_id` after each write | Low | Prevents duplicate rows reaching Superset |
| Medium | Add `completion_pct BETWEEN 0 AND 100` check after write | Low | Catches division/rounding bugs |
| Medium | Schema fingerprint check at pipeline startup | Medium | Catches source DB schema drift before writing |
| Medium | `subject_combos` JSON structure validation (check `JSON_VALID`) | Low | Catches serialisation errors |
| Low | Switch full rebuild from DROP+CREATE to TRUNCATE+INSERT | Medium | Preserves indexes across full rebuilds |
| Low | Add Slack/webhook alert on pipeline failure (in addition to email) | Medium | Faster incident response |
| Low | Migrate transformation logic to dbt models | High | Full lineage graph, native testing, CI integration |
