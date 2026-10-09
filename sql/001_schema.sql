-- =====================================================================
-- DEV_SKILL  |  FULL SCHEMA  |  PostgreSQL (Supabase)
-- Run top to bottom, once. Order matters (foreign keys).
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- 0. HELPERS
-- ---------------------------------------------------------------------
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin new.updated_at = now(); return new; end $$;

create sequence if not exists centre_seq;

create or replace function next_centre_code() returns text
language sql as $$
  select 'DSK-WB-' || lpad(nextval('centre_seq')::text, 4, '0')
$$;

-- Tracking code for public applications, e.g. APP-7K3M9XQ2
create or replace function gen_tracking_code() returns text
language plpgsql as $$
declare chars text := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; code text := ''; i int;
begin
  for i in 1..8 loop
    code := code || substr(chars, 1 + floor(random()*length(chars))::int, 1);
  end loop;
  return 'APP-' || code;
end $$;

-- ---------------------------------------------------------------------
-- 1. SCHEMES AND TRADE TEMPLATES (govt-defined, drive the detector)
-- ---------------------------------------------------------------------
create table schemes (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  code        text not null unique,
  description text,
  active      boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table trade_templates (
  id               uuid primary key default gen_random_uuid(),
  scheme_id        uuid not null references schemes(id) on delete restrict,
  trade_name       text not null,
  detector_classes jsonb not null check (jsonb_typeof(detector_classes) = 'array'),
  equipment_rules  jsonb not null check (jsonb_typeof(equipment_rules) = 'array'),
  presence_rule    text not null default 'seated'
                   check (presence_rule in ('seated','workstation','any')),
  count_tolerance  int  not null default 1  check (count_tolerance between 0 and 5),
  agree_ratio      numeric(3,2) not null default 0.60 check (agree_ratio between 0.50 and 1.00),
  min_confidence   numeric(3,2) not null default 0.35 check (min_confidence between 0.05 and 0.95),
  base_frames      int  not null default 10 check (base_frames >= 5),
  step_frames      int  not null default 5  check (step_frames >= 1),
  max_frames       int  not null default 40,
  active           boolean not null default true,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (scheme_id, trade_name),
  check (max_frames >= base_frames)
);

-- ---------------------------------------------------------------------
-- 2. APPLICATIONS (created before centres; centre_id FK added later)
-- ---------------------------------------------------------------------
create table applications (
  id                    uuid primary key default gen_random_uuid(),
  tracking_code         text not null unique default gen_tracking_code(),
  applicant_name        text not null,
  applicant_type        text not null check (applicant_type in ('municipality','panchayat','ngo','other')),
  contact_phone         text not null,
  contact_email         text,
  centre_name           text not null,
  scheme_id             uuid not null references schemes(id),
  trade_template_id     uuid not null references trade_templates(id),
  trainers              jsonb not null default '[]'::jsonb,
  max_trainees          int  not null check (max_trainees > 0),
  equipment             jsonb not null default '[]'::jsonb,
  address               text not null,
  district              text not null,
  state                 text not null,
  lat                   numeric(9,6),
  lng                   numeric(9,6),
  room_info             jsonb,
  internet_availability text check (internet_availability in ('none','intermittent','stable')),
  proposed_batches      jsonb,
  documents             jsonb,
  status                text not null default 'submitted'
                        check (status in ('submitted','under_review','query_raised',
                                          'resubmitted','approved','rejected')),
  review_comment        text,
  reviewed_by           uuid,
  centre_id             uuid,
  created_by_govt       boolean not null default false,
  submitted_at          timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

create table application_events (
  id             uuid primary key default gen_random_uuid(),
  application_id uuid not null references applications(id) on delete cascade,
  actor          uuid,
  action         text not null,
  comment        text,
  ts             timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 3. CENTRES AND PROFILES
-- ---------------------------------------------------------------------
create table centres (
  id                  uuid primary key default gen_random_uuid(),
  code                text not null unique,
  application_id      uuid references applications(id) on delete set null,
  name                text not null,
  scheme_id           uuid not null references schemes(id),
  trade_template_id   uuid not null references trade_templates(id),
  max_trainees        int  not null check (max_trainees > 0),
  address             text,
  district            text,
  state               text,
  lat                 numeric(9,6),
  lng                 numeric(9,6),
  contact_phone       text,
  contact_email       text,
  status              text not null default 'pending_setup'
                      check (status in ('pending_setup','awaiting_camera_approval',
                                        'live','suspended','terminated')),
  status_reason       text,
  risk_score          numeric(5,2) not null default 0,
  live_since          timestamptz,
  created_by_govt     boolean not null default false,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

alter table applications
  add constraint fk_app_centre foreign key (centre_id) references centres(id) on delete set null;

create table profiles (
  id                       uuid primary key references auth.users(id) on delete cascade,
  role                     text not null check (role in
                       ('super_admin','scheme_officer','auditor','centre_admin','trainer')),
  centre_id                uuid references centres(id) on delete cascade,
  full_name                text not null,
  login_id                 text unique,
  phone                    text,
  must_change_password     boolean not null default true,
  temp_password_expires_at timestamptz,
  active                   boolean not null default true,
  last_login_at            timestamptz,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now(),
  check ((role in ('centre_admin','trainer')) = (centre_id is not null))
);

alter table applications
  add constraint fk_app_reviewer foreign key (reviewed_by) references profiles(id) on delete set null;

-- ---------------------------------------------------------------------
-- 4. CENTRE MASTER DATA
-- ---------------------------------------------------------------------
create table centre_equipment (
  id             uuid primary key default gen_random_uuid(),
  centre_id      uuid not null references centres(id) on delete cascade,
  class          text not null,
  label          text,
  sanctioned_qty int  not null check (sanctioned_qty >= 0),
  declared_qty   int  not null default 0 check (declared_qty >= 0),
  updated_at     timestamptz not null default now(),
  unique (centre_id, class)
);

create table staff (
  id               uuid primary key default gen_random_uuid(),
  centre_id        uuid not null references centres(id) on delete cascade,
  profile_id       uuid unique references profiles(id) on delete set null,
  full_name        text not null,
  staff_role       text not null check (staff_role in ('trainer','official')),
  phone            text,
  face_consent     boolean not null default false,
  face_consent_at  timestamptz,
  face_enrolled    boolean not null default false,
  active           boolean not null default true,
  created_at       timestamptz not null default now(),
  check (not face_enrolled or face_consent)
);

create table staff_faces (
  staff_id      uuid primary key references staff(id) on delete cascade,
  embedding_enc bytea not null,
  model         text,
  created_at    timestamptz not null default now()
);

create table batches (
  id          uuid primary key default gen_random_uuid(),
  centre_id   uuid not null references centres(id) on delete cascade,
  name        text not null,
  trainer_id  uuid references staff(id) on delete set null,
  weekdays    int[] not null check (weekdays <@ array[1,2,3,4,5,6,7] and cardinality(weekdays) > 0),
  start_time  time not null,
  end_time    time not null,
  start_date  date,
  end_date    date,
  active      boolean not null default true,
  created_at  timestamptz not null default now(),
  check (end_time > start_time),
  check (end_date is null or start_date is null or end_date >= start_date)
);

create table holidays (
  id           uuid primary key default gen_random_uuid(),
  centre_id    uuid not null references centres(id) on delete cascade,
  holiday_date date not null,
  reason       text,
  unique (centre_id, holiday_date)
);

create table trainees (
  id          uuid primary key default gen_random_uuid(),
  centre_id   uuid not null references centres(id) on delete cascade,
  batch_id    uuid references batches(id) on delete set null,
  full_name   text not null,
  external_id text,
  active      boolean not null default true,
  created_at  timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 5. SESSIONS AND ATTENDANCE (CLAIMED side)
-- ---------------------------------------------------------------------
create table sessions (
  id            uuid primary key default gen_random_uuid(),
  centre_id     uuid not null references centres(id) on delete cascade,
  batch_id      uuid not null references batches(id) on delete cascade,
  session_date  date not null,
  start_ts      timestamptz not null,
  end_ts        timestamptz not null,
  status        text not null default 'scheduled'
                check (status in ('scheduled','active','closed','missed')),
  planned_captures int not null default 0,
  done_captures    int not null default 0,
  unique (batch_id, session_date),
  check (end_ts > start_ts)
);

create table attendance_claims (
  id                  uuid primary key default gen_random_uuid(),
  session_id          uuid not null unique references sessions(id) on delete cascade,
  centre_id           uuid not null references centres(id) on delete cascade,
  claimed_count       int  not null check (claimed_count >= 0),
  present_trainee_ids uuid[] not null default '{}',
  marked_by           uuid references profiles(id),
  marked_at           timestamptz not null default now(),
  edit_reason         text,
  edit_count          int not null default 0
);

create table staff_attendance (
  id         uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions(id) on delete cascade,
  staff_id   uuid not null references staff(id) on delete cascade,
  method     text not null default 'face' check (method in ('face','manual_override')),
  verified   boolean not null,
  score      numeric(5,4),
  attempts   int not null default 1,
  marked_at  timestamptz not null default now(),
  unique (session_id, staff_id)
);

-- ---------------------------------------------------------------------
-- 6. CAMERAS AND HEALTH
-- ---------------------------------------------------------------------
create table cameras (
  id               uuid primary key default gen_random_uuid(),
  centre_id        uuid not null references centres(id) on delete cascade,
  name             text not null,
  source_uri       text not null,
  resolution       text,
  roi_polygon      jsonb,
  zones            jsonb,
  reference_phash  text,
  setup_status     text not null default 'draft'
                   check (setup_status in ('draft','submitted','approved','rejected')),
  reject_reason    text,
  submitted_at     timestamptz,
  decided_by       uuid references profiles(id),
  decided_at       timestamptz,
  status           text not null default 'unknown'
                   check (status in ('unknown','online','offline','tampered')),
  last_tamper_reason text,
  last_seen        timestamptz,
  created_at       timestamptz not null default now()
);

create table camera_health_events (
  id         bigserial primary key,
  camera_id  uuid not null references cameras(id) on delete cascade,
  status     text not null,
  reason     text,
  ts         timestamptz not null default now()
);

create table frame_fingerprints (
  id          bigserial primary key,
  camera_id   uuid not null references cameras(id) on delete cascade,
  session_id  uuid references sessions(id) on delete cascade,
  dhash       text not null,
  captured_at timestamptz not null default now()
);

-- ---------------------------------------------------------------------
-- 7. EDGE LEDGER AND OBSERVATIONS (OBSERVED side)
-- ---------------------------------------------------------------------
create table edge_devices (
  device_id      text primary key,
  centre_id      uuid references centres(id) on delete set null,
  public_key_hex text not null,
  last_sync      timestamptz,
  chain_ok       boolean not null default true,
  created_at     timestamptz not null default now()
);

create table ledger_entries (
  id        uuid primary key default gen_random_uuid(),
  device_id text   not null references edge_devices(device_id),
  seq       bigint not null,
  kind      text   not null,
  payload   jsonb  not null,
  prev_hash text   not null,
  hash      text   not null unique,
  sig       text   not null,
  ts        timestamptz not null,
  unique (device_id, seq)
);

create table observations (
  id              uuid primary key default gen_random_uuid(),
  centre_id       uuid not null references centres(id) on delete cascade,
  session_id      uuid references sessions(id) on delete cascade,
  camera_id       uuid references cameras(id) on delete set null,
  hour_bucket     timestamptz not null,
  observed_count  int,
  ci_low          int,
  ci_high         int,
  state           text not null check (state in ('ok','uncertain')),
  frames_used     int,
  per_frame_counts int[],
  equipment       jsonb,
  model_version   text,
  ledger_hash     text unique,
  created_at      timestamptz not null default now()
);

create table flags (
  id              uuid primary key default gen_random_uuid(),
  centre_id       uuid references centres(id) on delete cascade,
  session_id      uuid references sessions(id) on delete cascade,
  observation_id  uuid references observations(id) on delete cascade,
  type            text not null,
  severity        text not null check (severity in ('low','medium','high','critical')),
  state           text not null check (state in ('flagged','uncertain')),
  status          text not null default 'open'
                  check (status in ('open','acknowledged','centre_responded','under_review','resolved','dismissed','escalated')),
  reason          text not null,
  details         jsonb,
  evidence_paths  text[],
  model_version   text,
  ledger_hash     text unique,
  created_at      timestamptz not null default now()
);

create table flag_events (
  id             uuid primary key default gen_random_uuid(),
  flag_id        uuid references flags(id) on delete cascade,
  actor          uuid,
  actor_role     text,
  action         text,
  comment        text,
  attachments    jsonb,
  ts             timestamptz not null default now()
);

create table repair_declarations (
  id                   uuid primary key default gen_random_uuid(),
  centre_id            uuid references centres(id) on delete cascade,
  equipment_class      text,
  qty                  int,
  reason               text,
  expected_fix_date    date,
  status               text default 'pending'
                       check (status in ('pending','approved','rejected','expired')),
  approved_until       date,
  decided_by           uuid,
  created_at           timestamptz not null default now()
);

create table change_requests (
  id                 uuid primary key default gen_random_uuid(),
  centre_id          uuid references centres(id) on delete cascade,
  kind               text check (kind in ('equipment','enrolment','batch','camera')),
  payload            jsonb,
  reason             text,
  status             text default 'pending'
                     check (status in ('pending','approved','rejected')),
  decided_by         uuid,
  decision_comment   text,
  created_at         timestamptz not null default now()
);

create table notices (
  id            uuid primary key default gen_random_uuid(),
  centre_id     uuid references centres(id) on delete cascade,
  level         text check (level in ('advisory','notice','warning','suspension','termination')),
  subject       text,
  body          text,
  flag_ids      uuid[],
  issued_by     uuid,
  reply         text,
  replied_at    timestamptz,
  created_at    timestamptz not null default now()
);

create table audit_log (
  id            bigserial primary key,
  actor         uuid,
  actor_role    text,
  action        text,
  entity        text,
  entity_id     text,
  meta          jsonb,
  ts            timestamptz not null default now()
);

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
alter table profiles enable row level security;
alter table schemes enable row level security;
alter table trade_templates enable row level security;
alter table applications enable row level security;
alter table application_events enable row level security;
alter table centres enable row level security;
alter table centre_equipment enable row level security;
alter table staff enable row level security;
alter table staff_faces enable row level security;
alter table batches enable row level security;
alter table holidays enable row level security;
alter table trainees enable row level security;
alter table sessions enable row level security;
alter table attendance_claims enable row level security;
alter table staff_attendance enable row level security;
alter table cameras enable row level security;
alter table camera_health_events enable row level security;
alter table frame_fingerprints enable row level security;
alter table edge_devices enable row level security;
alter table ledger_entries enable row level security;
alter table observations enable row level security;
alter table flags enable row level security;
alter table flag_events enable row level security;
alter table repair_declarations enable row level security;
alter table change_requests enable row level security;
alter table notices enable row level security;
alter table audit_log enable row level security;

-- Storage: create PRIVATE bucket "evidence" in the Supabase dashboard.