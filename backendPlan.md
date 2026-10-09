==================================================================
 dev_skill_backend: PLAN AND SPEC  (FastAPI + Supabase + YOLO)
 Give this block together with the "Backend key code" block.
==================================================================

RULES FOR THE AI BUILDER (read first)
------------------------------------------------------------------
 1. Python 3.10 or 3.11. FastAPI + Uvicorn. Sync endpoints (def) are
    fine; run heavy vision work in the scheduler/thread pool, never
    inside an async def.
 2. The Supabase SERVICE ROLE key is used only here. Never return it.
 3. Frontends only call this API. Every endpoint uses
    Depends(require(...roles...)) except the public ones listed.
 4. Centre-side roles may only touch rows whose centre_id equals their
    profile.centre_id. Call assert_centre_access() in every centre route.
 5. All timestamps stored in UTC (timestamptz). Display is IST in UI.
 6. Raw frames are NEVER written to disk or database. Only the blurred,
    annotated, resized JPEG thumbnail may be saved.
 7. Never store passwords. Generated temporary passwords are returned
    once in the approval response and not logged.
 8. Return errors as {"detail": "...", "code": "SOME_CODE"} with proper
    HTTP status (401 auth, 403 role, 404, 409 conflict, 422 validation).
 9. Do not invent tables or columns. Use only the schema in this file.
10. Every state-changing officer action writes to audit_log.
11. Keep thresholds in the trade template, not hard-coded.
12. Do not add features not listed. Ask before changing the schema.

FOLDER STRUCTURE
------------------------------------------------------------------
 dev_skill_backend/
   .env  requirements.txt  README.md
   sql/001_schema.sql
   models/            (yolo weights, auto-downloaded on first run)
   sample_videos/     (sewing_normal.mp4, sewing_few.mp4, dark.mp4 ...)
   data/              (edge.db sqlite, evidence/ temp thumbnails)
   eval/              (labels.csv, evaluate.py, results.json)
   app/
     main.py
     core/    config.py  deps.py  audit.py
     db/      supabase_client.py  local.py
     routers/ me.py applications.py centres.py schemes.py cameras.py
              schedule.py trainees.py attendance.py staff.py
              equipment.py requests.py flags.py notices.py reports.py
              edge.py (demo + sync controls)
     services/ credentials.py ledger.py sync.py consolidate.py
               flag_engine.py face.py plausibility.py scheduler.py
               pipeline.py
               vision/ detector.py quality.py cleanup.py privacy.py
                       equipment.py

ENVIRONMENT (.env)
------------------------------------------------------------------
 SUPABASE_URL=...
 SUPABASE_SERVICE_ROLE_KEY=...
 FERNET_KEY=...              # python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
 EDGE_SIGNING_KEY_HEX=...    # 64 hex chars (32 bytes). Generate once.
 DEVICE_ID=edge-001
 ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,https://<gov>.vercel.app,https://<center>.vercel.app
 FORCE_OFFLINE=false

REQUIREMENTS (requirements.txt)
------------------------------------------------------------------
 fastapi, uvicorn[standard], pydantic-settings, python-dotenv,
 supabase, cachetools, apscheduler, python-multipart, httpx,
 ultralytics, opencv-python-headless, numpy, pillow,
 insightface, onnxruntime, cryptography
 (If insightface fails to install on Windows, install Microsoft C++
  Build Tools, or switch to the "deepface" package; keep the same
  face.py interface: embed(image_bgr) -> np.ndarray or None.)

RUN AND EXPOSE
------------------------------------------------------------------
 uvicorn app.main:app --host 0.0.0.0 --port 8000
 ngrok http 8000        (use a reserved static domain if your ngrok
                         account has one; otherwise update
                         NEXT_PUBLIC_API_URL on Vercel after each restart)
 CORS must allow the exact frontend origins. Frontends send header
 "ngrok-skip-browser-warning: true" on every fetch.

AUTH MODEL
------------------------------------------------------------------
 - Browser logs in with supabase-js. Sends Bearer access token.
 - Backend validates token with sb.auth.get_user(token) (cached 60 s),
   loads profiles row, checks active.
 - profiles.role in: super_admin, scheme_officer, auditor,
                     centre_admin, trainer
 - must_change_password=true blocks every endpoint except
   POST /me/change-password and GET /me.
 - If must_change_password and temp_password_expires_at < now ->
   403 code CREDENTIALS_EXPIRED (officer must reset).
 - Centre login ID = centre code (e.g. DSK-WB-0001) ; trainer login ID
   = DSK-WB-0001-T01. Frontend converts to
   <lowercase id>@centres.devskill.in. Govt users use real email.

DATABASE SCHEMA (sql/001_schema.sql: run in Supabase SQL editor)
------------------------------------------------------------------
 create extension if not exists pgcrypto;
 create sequence if not exists centre_seq;

 create or replace function next_centre_code() returns text language sql as
 $$ select 'DSK-WB-' || lpad(nextval('centre_seq')::text, 4, '0') $$;

 create table profiles(
   id uuid primary key references auth.users(id) on delete cascade,
   role text not null check (role in ('super_admin','scheme_officer','auditor','centre_admin','trainer')),
   centre_id uuid, full_name text not null,
   must_change_password boolean not null default true,
   temp_password_expires_at timestamptz,
   active boolean not null default true,
   created_at timestamptz default now());

 create table schemes(id uuid primary key default gen_random_uuid(),
   name text not null, code text unique not null, active boolean default true);

 create table trade_templates(id uuid primary key default gen_random_uuid(),
   scheme_id uuid references schemes(id), trade_name text not null,
   detector_classes jsonb not null,       -- ["person","chair","sewing machine"]
   equipment_rules jsonb not null,        -- [{"class":"sewing machine","label":"Sewing machine","min_units_per_trainee":0.5,"operability":"in_use","required":true}]
   presence_rule text not null default 'seated' check (presence_rule in ('seated','workstation','any')),
   count_tolerance int not null default 1,
   agree_ratio numeric not null default 0.6,
   min_confidence numeric not null default 0.35,
   base_frames int not null default 10, step_frames int not null default 5,
   max_frames int not null default 40);

 create table applications(id uuid primary key default gen_random_uuid(),
   tracking_code text unique not null,
   applicant_name text not null, applicant_type text not null,   -- municipality|panchayat|ngo|other
   contact_phone text not null, contact_email text,
   centre_name text not null,
   scheme_id uuid references schemes(id), trade_template_id uuid references trade_templates(id),
   trainers jsonb not null,               -- [{"name":"","qualification":""}]
   max_trainees int not null check (max_trainees > 0),
   equipment jsonb not null,              -- [{"class":"chair","qty":30}]
   address text not null, district text not null, state text not null,
   lat numeric, lng numeric,
   room_info jsonb,                       -- {"size_sqft":..,"cameras":..,"resolution":"1080p"}
   internet_availability text check (internet_availability in ('none','intermittent','stable')),
   proposed_batches jsonb, documents jsonb,
   status text not null default 'submitted'
     check (status in ('submitted','under_review','query_raised','resubmitted','approved','rejected')),
   review_comment text, reviewed_by uuid, centre_id uuid,
   created_by_govt boolean default false, created_at timestamptz default now());

 create table centres(id uuid primary key default gen_random_uuid(),
   code text unique not null, application_id uuid, name text not null,
   scheme_id uuid, trade_template_id uuid, max_trainees int not null,
   address text, district text, state text, lat numeric, lng numeric,
   contact_phone text, contact_email text,
   status text not null default 'pending_setup'
     check (status in ('pending_setup','awaiting_camera_approval','live','suspended','terminated')),
   risk_score numeric default 0, created_at timestamptz default now());

 create table centre_equipment(id uuid primary key default gen_random_uuid(),
   centre_id uuid references centres(id) on delete cascade,
   class text not null, label text, sanctioned_qty int not null,
   declared_qty int not null default 0, unique(centre_id, class));

 create table staff(id uuid primary key default gen_random_uuid(),
   centre_id uuid references centres(id), profile_id uuid, full_name text not null,
   staff_role text check (staff_role in ('trainer','official')),
   face_consent boolean default false, face_enrolled boolean default false,
   active boolean default true);
 create table staff_faces(staff_id uuid primary key references staff(id) on delete cascade,
   embedding_enc bytea not null, model text, created_at timestamptz default now());

 create table batches(id uuid primary key default gen_random_uuid(),
   centre_id uuid references centres(id), name text, trainer_id uuid references staff(id),
   weekdays int[] not null,               -- 1=Mon..7=Sun
   start_time time not null, end_time time not null,
   start_date date, end_date date, active boolean default true);
 create table trainees(id uuid primary key default gen_random_uuid(),
   centre_id uuid references centres(id), batch_id uuid references batches(id),
   full_name text not null, external_id text, active boolean default true,
   created_at timestamptz default now());

 create table sessions(id uuid primary key default gen_random_uuid(),
   centre_id uuid references centres(id), batch_id uuid references batches(id),
   session_date date not null, start_ts timestamptz not null, end_ts timestamptz not null,
   status text default 'scheduled' check (status in ('scheduled','active','closed','missed')),
   unique(batch_id, session_date));
 create table attendance_claims(id uuid primary key default gen_random_uuid(),
   session_id uuid unique references sessions(id), centre_id uuid,
   claimed_count int not null, present_trainee_ids uuid[],
   marked_by uuid, marked_at timestamptz default now(), edit_reason text);
 create table staff_attendance(id uuid primary key default gen_random_uuid(),
   session_id uuid references sessions(id), staff_id uuid references staff(id),
   method text default 'face', verified boolean not null,
   marked_at timestamptz default now(), unique(session_id, staff_id));

 create table cameras(id uuid primary key default gen_random_uuid(),
   centre_id uuid references centres(id), name text, source_uri text not null,
   resolution text, roi_polygon jsonb,    -- [[x,y],...] normalised 0..1
   zones jsonb,                           -- [{"name":"WS1","polygon":[[x,y],..]}]
   reference_phash text, setup_status text default 'draft'
     check (setup_status in ('draft','submitted','approved','rejected')),
   status text default 'unknown', last_seen timestamptz);

 create table ledger_entries(id uuid primary key default gen_random_uuid(),
   device_id text not null, seq bigint not null, kind text not null,
   payload jsonb not null, prev_hash text not null, hash text unique not null,
   sig text not null, ts timestamptz not null, unique(device_id, seq));
 create table edge_devices(device_id text primary key, centre_id uuid,
   public_key_hex text not null, last_sync timestamptz);

 create table observations(id uuid primary key default gen_random_uuid(),
   centre_id uuid, session_id uuid references sessions(id), camera_id uuid,
   hour_bucket timestamptz not null, observed_count int, ci_low int, ci_high int,
   state text check (state in ('ok','uncertain')), frames_used int,
   per_frame_counts int[], equipment jsonb, model_version text,
   ledger_hash text unique, created_at timestamptz default now());

 create table flags(id uuid primary key default gen_random_uuid(),
   centre_id uuid references centres(id), session_id uuid, observation_id uuid,
   type text not null, severity text not null check (severity in ('low','medium','high','critical')),
   state text not null check (state in ('flagged','uncertain')),
   status text not null default 'open'
     check (status in ('open','acknowledged','centre_responded','under_review','resolved','dismissed','escalated')),
   reason text not null, details jsonb, evidence_paths text[],
   model_version text, ledger_hash text unique, created_at timestamptz default now());
 create table flag_events(id uuid primary key default gen_random_uuid(),
   flag_id uuid references flags(id) on delete cascade, actor uuid, actor_role text,
   action text, comment text, attachments jsonb, ts timestamptz default now());

 create table repair_declarations(id uuid primary key default gen_random_uuid(),
   centre_id uuid, equipment_class text, qty int, reason text,
   expected_fix_date date, status text default 'pending'
     check (status in ('pending','approved','rejected','expired')),
   approved_until date, decided_by uuid, created_at timestamptz default now());
 create table change_requests(id uuid primary key default gen_random_uuid(),
   centre_id uuid, kind text check (kind in ('equipment','enrolment','batch','camera')),
   payload jsonb, reason text, status text default 'pending'
     check (status in ('pending','approved','rejected')),
   decided_by uuid, decision_comment text, created_at timestamptz default now());
 create table notices(id uuid primary key default gen_random_uuid(),
   centre_id uuid, level text check (level in ('advisory','notice','warning','suspension','termination')),
   subject text, body text, flag_ids uuid[], issued_by uuid,
   reply text, replied_at timestamptz, created_at timestamptz default now());
 create table audit_log(id bigserial primary key, actor uuid, actor_role text,
   action text, entity text, entity_id text, meta jsonb, ts timestamptz default now());

 -- Enrolment cap enforced in DB as well (race-safe):
 create or replace function enforce_cap() returns trigger language plpgsql as $$
 declare cnt int; mx int;
 begin
   select count(*) into cnt from trainees where centre_id=new.centre_id and active;
   select max_trainees into mx from centres where id=new.centre_id;
   if cnt >= mx then raise exception 'ENROLMENT_CAP_REACHED'; end if;
   return new;
 end $$;
 create trigger trg_cap before insert on trainees
   for each row execute function enforce_cap();

 -- RLS: enable on EVERY table, add NO policies (service role bypasses RLS)
 alter table profiles enable row level security;  -- repeat for all tables
 -- Storage: create PRIVATE bucket "evidence" in the Supabase dashboard.

API ENDPOINTS (prefix none; JSON)
------------------------------------------------------------------
 PUBLIC (rate-limit 20/min/IP)
  POST /public/applications              create application -> {tracking_code}
  GET  /public/applications/track?code=&phone=   status + review_comment
  PUT  /public/applications/{code}       resubmit after query (needs phone)
  GET  /public/schemes                   schemes + trade templates (names only)
 ME
  GET  /me                               profile + role + centre
  POST /me/change-password               {new_password} (min 10, mixed)
 GOVT (super_admin, scheme_officer; auditor = GET only)
  GET/POST/PUT /schemes ; /schemes/{id}/templates ; /templates/{id}
  GET  /applications?status=             list
  GET  /applications/{id}                detail + plausibility warnings
  POST /applications/{id}/approve        -> credentials (shown once)
  POST /applications/{id}/reject | /query    {comment}
  POST /centres                          govt direct create (same payload,
                                         also returns credentials once)
  GET  /centres?district=&status=&q=     list with risk + open flags
  GET  /centres/{id}                     full monitoring bundle
  POST /centres/{id}/reset-credentials   -> new one-time password
  POST /centres/{id}/status              {status, reason} (suspend/terminate
                                         only via notices ladder)
  GET  /centres/{id}/attendance?from=&to=   claimed vs observed series
  GET  /centres/{id}/equipment           sanctioned/declared/observed
  GET  /cameras?status=                  health board
  POST /cameras/{id}/approve | /reject
  GET  /flags?severity=&type=&status=&centre_id=
  GET  /flags/{id}                       detail + events + signed evidence URLs
  POST /flags/{id}/action                {action: acknowledge|resolve|dismiss|escalate, comment}
  GET  /requests (change requests + repair declarations)
  POST /requests/{id}/decide             {approve: bool, comment, approved_until?}
  POST /notices                          enforcement ladder step
  GET  /reports/rollup?by=district|scheme|trade
  GET  /audit-log?...                    (super_admin, auditor)
  GET  /accuracy                         contents of eval/results.json
  GET/POST /users (super_admin)          govt users
 CENTRE (centre_admin, trainer: scoped to own centre)
  GET  /my/centre                        profile, sanctioned, status, checklist
  POST /my/cameras ; PUT /my/cameras/{id}/setup (roi, zones) ; GET /my/cameras/{id}/reference-frame
  POST /my/cameras/{id}/submit           send setup for govt approval
  GET/POST/PUT /my/batches
  GET/POST /my/trainees  (409 ENROLMENT_CAP_REACHED)
  GET/POST /my/staff ; POST /my/staff/{id}/face-enrol {image_b64, consent:true}
  GET  /my/sessions/today
  POST /my/sessions/{id}/staff-scan      {image_b64}  (trainer, 1:1)
  POST /my/sessions/{id}/claim           {present_trainee_ids[]}
  GET/PUT /my/equipment                  declared quantities
  POST /my/repair-declarations ; POST /my/requests
  GET  /my/flags ; POST /my/flags/{id}/respond {comment, attachments}
  GET  /my/notices ; POST /my/notices/{id}/reply
 EDGE / DEMO (centre_admin, super_admin)
  POST /edge/demo/run   {camera_id, session_id, video, scenario}
  POST /edge/offline    {value: bool}
  POST /edge/sync-now
  GET  /edge/status     {online, unsynced_count, last_sync, chain_ok}
 SYSTEM
  GET /health

VISION PIPELINE SPEC
------------------------------------------------------------------
 detector.build(template): if every class in template.detector_classes
   exists in COCO -> YOLO("yolo11n.pt") ; else YOLOWorld("yolov8s-worldv2.pt")
   with set_classes(template.detector_classes). Optional: fine-tuned
   weights path if eval shows it beats baseline (keep held-out split).
 Tiling: run full frame + 2x2 overlapping tiles (overlap 20%), merge
   with class-wise NMS. Compare accuracy with/without tiling in eval.
 quality gate (per frame): too dark (mean<25), covered (std<12),
   blurry (laplacian var<40), frozen (abs diff to previous <0.5 with
   gap >=20 s), moved (phase-correlation shift >5% width vs reference).
   Bad frame -> retake up to 3 times; repeated -> C1 flag.
 cleanup: boxes kept only if reference point inside camera ROI
   (people: bottom-centre; objects: centre). Person counts if rule:
   seated -> overlaps a chair box ; workstation -> foot point inside a
   zone or overlaps workstation equipment ; any -> always.
   Subtract trainers verified present in this session (staff_attendance
   verified=true). If trainer did not scan, subtract 1 and raise S1.
 equipment status per class: count present in ROI; operability per rule:
   screen_on (mean brightness/variance in box), in_use (person overlaps
   machine box at least once in session), none. States: present_operable,
   present_idle, present_non_operable, absent.
 privacy: blur top 28% of every person box (head region), draw boxes,
   resize to 640 px width, JPEG quality 60, discard raw frame in memory.
 hourly consolidation: see consolidate() in key code.
 outputs per hour: observed_count, ci_low, ci_high, state, per-frame
   counts, equipment counts, model_version, best thumbnail path.

FLAG TYPES
------------------------------------------------------------------
 A1 Attendance inflation     claimed > ci_high + margin
 A2 Over capacity            ci_low > sanctioned max
 A3 Enrolment mismatch       claimed > enrolled trainees
 A4 Ghost session            claimed > 0 and observed 0 (state ok)
 A5 Unregistered attendees   observed > claimed + margin (low severity)
 E1 Equipment missing        ci_high < sanctioned_qty - approved repairs
 E2 Equipment non-operational  present_non_operable in >=60% frames
                               (suppressed by approved repair)
 E3 Declaration mismatch     declared_qty != sanctioned_qty
 S1 Trainer not verified     no face-verified trainer in session
 C1 Camera offline/covered/moved/frozen/dark
 C2 Replay suspicion         same frame fingerprint repeated across
                             different days/sessions
 C3 Missed capture windows   fewer captures than planned
 D1 Data integrity           hash chain or signature invalid (central)
 U1 Unverifiable count       observation UNCERTAIN after max frames
 P1 Capacity implausible     (approval time) equipment vs trainees
 margin = max(count_tolerance, ceil(0.10 * claimed))
 severity by gap ratio (<15% low, <30% medium, <50% high, else critical),
 +1 level if repeated >=3 times in 7 days.

BUILD ORDER FOR THE BACKEND
------------------------------------------------------------------
 1 config, supabase client, deps (auth), /me, change-password, audit
 2 schemes/templates, public applications, approve (credentials)
 3 centres, cameras, batches, trainees (cap), staff, equipment
 4 vision services standalone, tested with a script on sample videos
 5 consolidate + flag_engine unit tests (pure functions)
 6 ledger + sync + evidence upload
 7 scheduler + demo runner + staff face scan + claim
 8 flags router/actions, notices, requests, reports, accuracy
 9 eval script and results.json

TEST CHECKLIST (must pass before demo)
------------------------------------------------------------------
 - Trainer token cannot read another centre's data (403).
 - Enrolment above cap returns 409 ENROLMENT_CAP_REACHED.
 - Counts 5,7,10,2 -> not consistent -> more frames -> UNCERTAIN at cap.
 - Counts 6,6,5,6,7 -> consistent, observed 6.
 - Claimed 12 vs observed 7 (ci 6..8) -> A1 flagged.
 - Claimed 7 vs observed 7 -> no flag.
 - Black video -> C1, no A1 (never accuse on a bad camera).
 - Tampered ledger row -> verify_chain returns invalid index.
 - Offline toggle: records queue; after online they sync exactly once.
 - No raw frame file exists anywhere after a run.