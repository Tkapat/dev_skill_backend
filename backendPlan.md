==================================================================
 dev_skill_backend: PLAN AND SPEC  v2
 (FastAPI + MongoDB Atlas + YOLO + own JWT auth)
 Give this block together with: Backend key code, M1, M2, M3, M4 and the
 "Backend additions for Centre portal v2". Where this block and an older
 Supabase block disagree, THIS block wins.
==================================================================

RULES FOR THE AI BUILDER (read first, obey exactly)
------------------------------------------------------------------
 1. Python 3.10 or 3.11. FastAPI + Uvicorn. Sync endpoints (def) are
    fine; run heavy vision work in the scheduler/thread pool, never
    inside an async def. File upload endpoints may be async.
 2. There is NO Supabase anywhere. Database = MongoDB Atlas via PyMongo
    (sync), ONE cluster, ONE database "devskill". MONGODB_URI, JWT_SECRET,
    FERNET_KEY, FILE_URL_SECRET live only in backend .env and are never
    returned or logged.
 3. Frontends only call this API. Every endpoint uses
    Depends(require(...roles...)) except the PUBLIC ones listed.
 4. Centre-side roles may only touch documents whose centre_id equals
    user["centre_id"]. Call assert_centre_access() or filter by
    centre_id in EVERY centre route. Never trust a centre_id sent by
    the client; take it from the token's user.
 5. All timestamps = timezone-aware UTC datetimes. Date-only values are
    "YYYY-MM-DD" strings, time-of-day values are "HH:MM" strings.
 6. Raw frames are NEVER written to disk or database. Only blurred,
    annotated, resized JPEG thumbnails (<=60 KB) may be saved.
    The camera reference frame (for the ROI editor) is also blurred
    with the same privacy function before storage.
 7. Never store plain passwords. Generated temporary passwords are
    returned once in the response and never logged.
 8. Errors: {"detail": {"detail": "...", "code": "SOME_CODE"}} via
    HTTPException(status, detail={...}). 401 auth, 403 role/scope,
    404, 409 conflict, 422 validation, 429 rate limit.
 9. Do not invent collections or fields. Use only the shapes in the
    COLLECTIONS section. Ask before changing a shape.
10. Every state-changing officer/admin action writes to audit_log
    (insert-only, never update/delete it).
11. Thresholds live in trade_templates, not in code.
12. IDs: store ObjectId internally; return strings under key "id"
    (ser()). Parse incoming ids with oid() (422 BAD_ID on failure).
13. Lists: ?limit=25 (max 100) and ?cursor=<last id>, sorted by _id or
    created_at desc. Never skip(). Return {"items":[...],"next_cursor":..}.
14. No DB triggers exist: invariants are enforced in code
    (enrolment cap = atomic counter, approve = atomic claim).
15. Public routes are rate limited (slowapi, 20/min/IP; login 10/min).
16. Do not add features not listed.

FOLDER STRUCTURE
------------------------------------------------------------------
 dev_skill_backend/
   .env  requirements.txt  README.md
   scripts/init_db.py          (indexes, validators, seed, first admin)
   models/                     (yolo weights, auto-downloaded)
   sample_videos/              (sewing_normal.mp4 sewing_few.mp4 dark.mp4
                                frozen.mp4 equipment_removed.mp4 ...)
   data/                       (edge.db sqlite ledger, evidence/ temp)
   eval/                       (labels.csv evaluate.py results.json)
   app/
     main.py
     core/    config.py security.py deps.py audit.py limiter.py
     db/      mongo.py local.py
     routers/ auth.py (auth + /me*) applications.py centres.py
              schemes.py users.py cameras.py schedule.py trainees.py
              attendance.py staff.py equipment.py requests.py flags.py
              notices.py reports.py files.py edge.py
     services/ credentials.py ledger.py sync.py consolidate.py
               flag_engine.py face.py plausibility.py scheduler.py
               pipeline.py dbfuncs.py
               vision/ detector.py quality.py cleanup.py privacy.py
                       equipment.py

ENVIRONMENT (.env)
------------------------------------------------------------------
 MONGODB_URI=mongodb+srv://<user>:<urlencoded-pass>@<cluster>.mongodb.net/?retryWrites=true&w=majority
 MONGODB_DB=devskill
 JWT_SECRET=<64+ chars>        # python -c "import secrets;print(secrets.token_urlsafe(64))"
 FILE_URL_SECRET=<64+ chars>   # different from JWT_SECRET
 FERNET_KEY=...                # python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
 EDGE_SIGNING_KEY_HEX=...      # 64 hex chars (32 bytes), generate once
 DEVICE_ID=edge-001
 ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3001,https://<gov>.vercel.app,https://<center>.vercel.app
 FORCE_OFFLINE=false
 (.env is in .gitignore. Atlas user: readWrite on "devskill" only.)

REQUIREMENTS (requirements.txt)
------------------------------------------------------------------
 fastapi, uvicorn[standard], pydantic-settings, python-dotenv,
 pymongo[srv], PyJWT, argon2-cffi, pyotp, slowapi, cachetools,
 apscheduler, python-multipart, httpx, ultralytics,
 opencv-python-headless, numpy, pillow, insightface, onnxruntime,
 cryptography, torch/torchvision (pulled by ultralytics)
 (NO supabase package. If insightface fails on Windows install Microsoft
  C++ Build Tools, or use "deepface" behind the same face.py interface:
  embed(image_bgr) -> np.ndarray | None.)

RUN AND EXPOSE
------------------------------------------------------------------
 python -m scripts.init_db                 (once)
 uvicorn app.main:app --host 0.0.0.0 --port 8000
 ngrok http 8000   (reserved static domain if available; else update
                    NEXT_PUBLIC_API_URL on Vercel after each restart)
 CORS: exact frontend origins only. Frontends send header
 "ngrok-skip-browser-warning: true".
 Startup (main.py): sync.register_device(); scheduler.start().
 Scheduler jobs: sync_tick every 20 s; expire_repairs daily;
 generate_sessions nightly (all LIVE/pending centres, next 14 days);
 session status updater every 1 min (scheduled->active->closed/missed);
 capture_tick every 30 s for centres with live RTSP cameras (optional).

AUTH MODEL (own, replaces Supabase)
------------------------------------------------------------------
 - POST /auth/login {login_id, password}. Govt login_id = email.
   Centre login_id = DSK-WB-0001 / DSK-WB-0001-T01 (stored lowercase,
   compared case-insensitive). Same error for unknown ID and wrong
   password. 5 failures -> locked 15 min (429 ACCOUNT_LOCKED).
 - Govt roles with mfa_enabled get {mfa_required, mfa_token} then
   POST /auth/mfa/login {mfa_token, code}.
 - Tokens: access JWT 30 min (claims sub, tv, typ=access) + opaque
   refresh token 7 days (stored SHA-256 hashed in refresh_tokens, TTL
   index, ONE-TIME use, rotated by POST /auth/refresh).
 - Password change or reset bumps users.token_version and deletes the
   user's refresh tokens, so all old tokens die. /me/change-password
   returns fresh tokens.
 - must_change_password=true blocks every endpoint except GET /me and
   POST /me/change-password (require(..., allow_unchanged_password=True)).
   If temp_password_expires_at < now -> 403 CREDENTIALS_EXPIRED.
 - Password rule: >=10 chars, upper, lower, digit, special.
 - Roles: super_admin, scheme_officer, auditor (GET only),
   centre_admin, trainer.
 - Govt accounts are created only by super_admin (POST /users) or the
   init script. No self sign-up exists.
 - CORS + rate limits + lockout are mandatory, not optional.

COLLECTIONS (the only data shapes allowed)
------------------------------------------------------------------
 Notation: oid = ObjectId, dt = UTC datetime, d = "YYYY-MM-DD",
 t = "HH:MM". Indexes/validators/TTLs are in scripts/init_db.py (M2).

 users {_id, login_id(unique,lowercase), password_hash, role, centre_id|null,
   full_name, phone, must_change_password, temp_password_expires_at|null,
   active, token_version, failed_attempts, locked_until|null,
   mfa_enabled, mfa_secret_enc?, mfa_pending_enc?, notice_accepted_at?,
   last_login_at?, created_at}
 refresh_tokens {user_id, token_hash(unique), expires_at(TTL), created_at}
 counters {_id:"centre"|"trainer:<centre_code>", n}

 schemes {name, code(unique), description, active, created_at}
 trade_templates {scheme_id, trade_name, detector_classes[str],
   equipment_rules[{class,label,min_units_per_trainee,operability:
   none|screen_on|in_use,required}], presence_rule: seated|workstation|any,
   count_tolerance(0-5), agree_ratio(0.5-1), min_confidence(0.05-0.95),
   base_frames(>=5), step_frames(>=1), max_frames(>=base), active}

 applications {tracking_code(unique), applicant_name, applicant_type:
   municipality|panchayat|ngo|other, contact_phone, contact_email?,
   centre_name, scheme_id, trade_template_id,
   trainers[{name,qualification}], max_trainees, equipment[{class,label,qty}],
   address, district, state, lat?, lng?,
   room_info{size_sqft,cameras,resolution}, internet_availability:
   none|intermittent|stable, proposed_batches[], documents[{name,file_id,
   size,content_type}], status: submitted|under_review|query_raised|
   resubmitted|approved|rejected, review_comment?, reviewed_by?, centre_id?,
   created_by_govt, events[{actor?,action,comment?,ts}], submitted_at,
   updated_at}

 centres {code(unique), application_id, name, scheme_id, trade_template_id,
   max_trainees, enrolled_count, address, district, state, lat, lng,
   contact_phone, contact_email, equipment[{class,label,sanctioned_qty,
   declared_qty}], status: pending_setup|awaiting_camera_approval|live|
   suspended|terminated, status_reason?, risk_score, live_since?,
   submitted_at?, created_by_govt, created_at, updated_at}

 staff {centre_id, user_id?, full_name, staff_role: trainer|official, phone,
   face_consent, face_consent_at?, face_enrolled, active, created_at}
 staff_faces {_id = staff _id, embedding_enc(Binary), model, created_at}
   STAFF ONLY. Encrypted embedding. Never images. Never trainees.
 batches {centre_id, name, trainer_id, weekdays[1..7], start_time(t),
   end_time(t), start_date?(d), end_date?(d), active, created_at}
 holidays {centre_id, holiday_date(d), reason}
 trainees {centre_id, batch_id?, full_name, external_id?, active,
   created_at}      (NO photo / face / biometric fields. Ever.)

 sessions {centre_id, batch_id, session_date(d), start_ts, end_ts,
   status: scheduled|active|closed|missed, planned_captures, done_captures}
 attendance_claims {session_id(unique), centre_id, claimed_count,
   present_trainee_ids[], marked_by, marked_at, edit_reason?, edit_count}
 staff_attendance {session_id, staff_id, method: face|manual_override,
   verified, score?, attempts, marked_at}  (unique session+staff)

 cameras {centre_id, name, source_uri, resolution, roi_polygon[[x,y]]|null,
   zones[{name,polygon}]|null, reference_phash?, reference_blob_id?,
   setup_status: draft|submitted|approved|rejected, reject_reason?,
   submitted_at?, decided_by?, decided_at?, status: unknown|online|offline|
   tampered, last_tamper_reason?, last_seen?, created_at}
 camera_health_events {camera_id, status, reason, ts(TTL 90d)}
 frame_fingerprints {camera_id, session_id, dhash, captured_at(TTL 60d)}

 edge_devices {_id=device_id, centre_id?, public_key_hex, last_sync,
   chain_ok, created_at}
 ledger_entries {device_id, seq, kind, payload, prev_hash, hash(unique),
   sig, ts}
 observations {centre_id, session_id, camera_id, hour_bucket,
   observed_count|null, ci_low, ci_high, state: ok|uncertain, frames_used,
   per_frame_counts[], equipment{class:{observed,ci_low,ci_high,
   consistent,status}}, tamper[], model_version, ledger_hash(unique)}
 evidence_blobs {_id: string (<hash16>_<i> or "ref_<camera_id>"), centre_id,
   session_id?, kind: evidence|reference, data(Binary<=60KB), created_at
   (TTL 90d)}

 flags {centre_id, session_id?, observation_id?, type(A1..P1), severity,
   state: flagged|uncertain, status: open|acknowledged|centre_responded|
   under_review|resolved|dismissed|escalated, reason, details{claimed,
   observed,ci_low,ci_high,margin,threshold,per_frame_counts}, evidence_ids[],
   model_version, ledger_hash(unique, sparse), events[{actor?,actor_role?,
   action,comment?,attachments?,ts}], resolved_at?, created_at, updated_at}
 repair_declarations {centre_id, equipment_class, qty, reason,
   expected_fix_date(d), status: pending|approved|rejected|expired,
   approved_until?(d), decided_by?, decision_comment?, created_at}
 change_requests {centre_id, kind: equipment|enrolment|batch|camera,
   payload, reason, status: pending|approved|rejected, decided_by?,
   decision_comment?, decided_at?, created_at}
 notices {centre_id, level: advisory|notice|warning|suspension|
   termination, subject, body, flag_ids[], issued_by, read_at?, reply?,
   replied_at?, created_at}
 audit_log {actor, actor_role, action, entity, entity_id, meta, ts}
 GridFS bucket "documents": application uploads (PDF/JPG/PNG, <=5 MB).

 RESPONSE RULES: every flag payload also returns evidence_urls[] =
 sign_file_url("evidence", id) for each evidence_ids entry; documents
 return url = sign_file_url("document", file_id). Never expose raw ids
 as URLs. Never return password_hash, mfa secrets or embeddings.

API ENDPOINTS (JSON; * = write)
------------------------------------------------------------------
 PUBLIC (rate limit 20/min/IP)
  POST /public/applications              -> {tracking_code}
  POST /public/applications/{code}/documents?phone=   multipart "file"
                                         (max 5 files, 5 MB, pdf/jpg/png)
  GET  /public/applications/track?code=&phone=   status, events,
                                         review_comment (never credentials;
                                         generic 404 if code/phone wrong)
  PUT  /public/applications/{code}       resubmit after query (needs phone);
                                         sets status resubmitted + event
  GET  /public/schemes                   schemes + trade templates
                                         (names, equipment_rules for prefill)
 AUTH
  POST /auth/login | /auth/mfa/login | /auth/refresh | /auth/logout
 ME (any role)
  GET  /me                               profile + centre{code,name,status}
  POST /me/change-password*              {current_password,new_password,
                                          accepted_notice?} -> fresh tokens
  POST /me/mfa/setup* | /me/mfa/enable*  (govt roles)
 FILES
  GET  /files/{kind}/{id}?exp=&sig=      HMAC-signed, 60 s, kind=evidence|document
 GOVT (super_admin, scheme_officer write; auditor GET only)
  GET/POST*/PUT* /schemes ; /schemes/{id}/templates ; /templates/{id}
                                         (create/edit super_admin only)
  GET  /applications?status=&q=          list (+ counts per status)
  GET  /applications/{id}                detail + plausibility warnings
                                         + signed document urls
  POST /applications/{id}/approve*       -> {centre_id, login_id,
                                          temporary_password, expires_in_hours}
                                          atomic; 409 BAD_STATE on repeat
  POST /applications/{id}/reject* | /query*   {comment} (comment required)
  POST /centres*                         govt direct create (same payload as
                                         application) -> credentials once
  GET  /centres?district=&status=&q=&sort=risk&limit=   list with risk,
                                         open_flags, enrolled, camera_status,
                                         last_session (grouped aggregations,
                                         no N+1)
  GET  /centres/{id}                     full monitoring bundle
  POST /centres/{id}/reset-credentials*  -> new one-time password
  GET  /centres/{id}/attendance?from=&to=   attendance_series() output
  GET  /centres/{id}/equipment           sanctioned / declared / observed
                                         (+ status per class)
  GET  /centres/{id}/evidence            thumbnails list with signed urls
  GET  /centres/{id}/timeline            audit entries for this centre
  GET  /cameras?status=&setup_status=    health board
  POST /cameras/{id}/approve* | /reject*  {reason} on reject; when the
                                         centre has approved cameras and was
                                         awaiting approval -> status live,
                                         live_since set
  GET  /flags?severity=&type=&status=&state=&centre_id=&district=&limit=&cursor=
  GET  /flags/{id}                       detail + events + evidence_urls[]
  POST /flags/{id}/action*               {action: acknowledge|resolve|dismiss|
                                          escalate, comment} (dismiss and
                                          resolve need comment); recompute_risk
  POST /flags/bulk-acknowledge*          {ids[]}
  GET  /requests?kind=&status=           change requests + repair declarations
  POST /requests/{id}/decide*            {approve, comment, approved_until?}
                                         effects: equipment -> increase
                                         centres.equipment[].sanctioned_qty;
                                         enrolment -> $inc max_trainees;
                                         batch -> create batch + sessions;
                                         camera -> allow camera; repair ->
                                         approved_until capped at today+30d
  POST /notices*                         enforcement ladder step. suspension ->
                                         centres.status suspended;
                                         termination -> terminated (typed
                                         confirmation enforced in UI, reason
                                         stored in status_reason). Also
                                         invalidates nothing else.
  GET  /notices?centre_id=               ladder view
  GET  /reports/rollup?by=district|scheme|trade
  GET  /audit-log?actor=&entity=&from=&to=   (super_admin, auditor)
  GET  /accuracy                         contents of eval/results.json
  GET/POST* /users ; POST /users/{id}/deactivate*   (super_admin only;
                                         POST returns temp password once)
 CENTRE (centre_admin, trainer; scoped to own centre; trainers limited)
  GET  /my/centre                        centre, sanctioned equipment, status,
                                         checklist{camera,batches,staff,
                                         equipment,submitted}, submitted_at
  POST /my/cameras* ; GET /my/cameras ; PUT /my/cameras/{id}/setup*
       {roi_polygon, zones} (validate normalised 0..1, >=3 points, area>0.02)
  GET  /my/cameras/{id}/reference-frame  {url, width, height, quality{
       brightness,sharpness,coverage}} (blurred frame, signed url)
  POST /my/cameras/{id}/submit*          needs saved roi; sets submitted;
                                         centre -> awaiting_camera_approval
  GET/POST*/PUT* /my/batches             after post/put -> generate_sessions;
                                         after approval, creating a NEW batch
                                         returns 403 NEEDS_APPROVAL (use
                                         change request); edits to existing
                                         batches allowed and audit-logged
  GET/POST* /my/holidays ; DELETE* /my/holidays/{id}
  GET  /my/trainees?batch=&q=&limit=&cursor=
  POST /my/trainees*                     take_seat(); 409 ENROLMENT_CAP_REACHED
  POST /my/trainees/bulk*                {rows[]} stops at cap, returns added
  POST /my/trainees/{id}/deactivate* | /reactivate*   (reactivate uses take_seat)
  GET  /my/staff ; POST /my/staff*       creates staff + trainer login ->
                                         {login_id, temporary_password} once;
                                         409 LOGIN_EXISTS
  POST /my/staff/{id}/deactivate*        deletes staff_faces, user inactive
  POST /my/staff/{id}/face-enrol*        {image_b64, consent:true}; only that
                                         staff member or centre_admin
  DELETE /my/staff/{id}/face*
  GET  /my/sessions/today ; GET /my/sessions?from=&to=
  POST /my/sessions/{id}/staff-scan*     {image_b64, frames_b64?}; 1:1 vs the
                                         LOGGED-IN user's staff embedding only;
                                         window start-15min..end; counts
                                         attempts; 3 fails -> verified=false
  POST /my/sessions/{id}/claim*          {present_trainee_ids[], edit_reason?}
                                         409 CLAIM_LOCKED without edit_reason
                                         on re-submit; ids must belong to centre
  GET/PUT* /my/equipment                 declared quantities
  POST /my/repair-declarations* ; GET /my/repair-declarations
                                         (expected_fix_date <= today+30d)
  POST /my/requests* ; GET /my/requests
  GET  /my/flags?limit= ; POST /my/flags/{id}/respond*   {comment,
                                         attachments?}; pushes event
                                         "centre_respond", status
                                         centre_responded
  GET  /my/notices?unread= ; POST /my/notices/{id}/read* | /reply*
  Suspended/terminated centres: every write returns 403 CENTRE_INACTIVE.
 EDGE / DEMO (centre_admin, super_admin)
  GET  /edge/videos                      names in sample_videos/
  POST /edge/demo/run*   {camera_id, session_id, video, scenario,
                          claimed_override?}  -> {observed, ci, claimed,
                          per_frame, thumbs urls, flags[]}
  POST /edge/offline*    {value: bool}
  POST /edge/sync-now*
  GET  /edge/status      {online, unsynced_count, last_sync, chain_ok}
 SYSTEM
  GET /health            (public)

VISION PIPELINE SPEC
------------------------------------------------------------------
 detector.build(template): if every class in template.detector_classes
   is a COCO class -> YOLO("yolo11n.pt"); else YOLOWorld(
   "yolov8s-worldv2.pt") with set_classes(template.detector_classes).
   Optional fine-tuned weights only if eval shows they beat baseline on
   a held-out split. Load models once per process (cache by class set).
 Tiling: full frame + 2x2 overlapping tiles (about 20% overlap), merge
   with class-wise NMS. Compare with/without tiling in eval.
 Quality gate per frame: dark (mean<25), covered (std<12), blurry
   (laplacian var<40), frozen (mean abs diff to previous <0.5 with gap
   >=20 s), moved (phase-correlation shift >5% of width vs reference).
   Bad frame -> retake up to 3 times; repeated -> C1 flag and counts
   are NOT evaluated (never accuse on a bad camera).
 Replay (C2): store dhash of accepted frames; same/near hash (hamming
   <=3) from a different day or session on the same camera -> C2.
 Cleanup: keep boxes whose reference point is inside the camera ROI
   (people: bottom-centre; objects: centre). Person counts by rule:
   seated -> overlaps a chair box; workstation -> foot point in a zone
   or overlaps workstation equipment; any -> always.
   Subtract trainers/officials verified present in this session
   (staff_attendance verified=true). If none verified, subtract 1 and
   raise S1.
 Equipment status per class: count in ROI; operability per template
   rule: screen_on (brightness/variance in box), in_use (person overlaps
   the box in >=1 frame of the session), none. States: present_operable,
   present_idle, present_non_operable, absent.
 Privacy: blur top 28% of every person box, draw boxes, resize to
   640 px width, JPEG q=60, <=60 KB, drop raw frame immediately.
 Consolidation: consolidate() with tolerance and agree_ratio from the
   template; add frames in steps up to max_frames; then UNCERTAIN.
 Outputs: observation doc + up to 3 thumbnails per run; one ledger
   entry per observation and per flag.
 Demo scenarios choose a different sample video (normal, few_people,
   equipment_removed, dark, frozen).

FLAG TYPES (flag_engine.evaluate)
------------------------------------------------------------------
 A1 Attendance inflation     claimed > ci_high + margin
 A2 Over capacity            ci_low > sanctioned max
 A3 Enrolment mismatch       claimed > enrolled trainees
 A4 Ghost session            claimed > 0 and observed 0 (state ok)
 A5 Unregistered attendees   observed > claimed + margin (low)
 E1 Equipment missing        ci_high < sanctioned_qty - approved repairs
 E2 Equipment non-operational  present_non_operable in >=60% of frames
                               (suppressed by approved repair)
 E3 Declaration mismatch     declared_qty != sanctioned_qty
 S1 Trainer not verified     no face-verified staff in session
 C1 Camera offline/covered/moved/frozen/dark
 C2 Replay suspicion         repeated frame fingerprint
 C3 Missed capture windows   done_captures < planned_captures
 D1 Data integrity           hash chain/signature invalid (verified on sync)
 U1 Unverifiable count       UNCERTAIN after max frames (state=uncertain)
 P1 Capacity implausible     computed at approval time (warning only)
 margin = max(count_tolerance, ceil(0.10 * claimed))
 severity by gap ratio (<15% low, <30% medium, <50% high, else critical),
 +1 level if the same type repeats >=3 times in 7 days.
 After every new flag or flag status change: recompute_risk(centre_id).
 Flag action transitions allowed: open -> acknowledged | under_review |
 resolved | dismissed | escalated; centre_responded -> any officer
 action; resolved/dismissed are final. Dismissals are the false-positive
 log used by /reports and /accuracy.

INTEGRITY AND SYNC
------------------------------------------------------------------
 - Every claim, observation and flag is appended to the local SQLite
   ledger (SHA-256 chain + Ed25519 signature) BEFORE it is synced.
 - sync_tick (every 20 s, max_instances=1): flags first, idempotent
   upserts keyed by ledger hash, evidence ids deterministic
   (<hash16>_<i>) so retries never duplicate, local thumbnail deleted
   after upload, stops on first error and retries later.
 - register_device() upserts edge_devices at startup.
 - Central verify: on each sync batch, verify_chain on the device rows;
   invalid -> edge_devices.chain_ok=false and a D1 flag.
 - "Offline" toggle (Demo Lab) sets sync STATE; records queue in SQLite.
 - Login, claims and the dashboards need Atlas; capture, pipeline and
   ledger work offline.

PRIVACY AND SECURITY CHECKLIST
------------------------------------------------------------------
 - Trainees: no face or photo anywhere. Staff: consent flag required
   before enrol; embedding encrypted (Fernet); deleted on deactivate or
   "Remove face data"; verification only against the caller's own record.
 - Face images are decoded in memory, never written, never logged.
 - Evidence auto-expires (TTL 90 d). Signed URLs 60 s. No public file
   access without a valid signature.
 - Argon2, lockout, rate limits, rotating refresh tokens, token_version.
 - Dummy password hash verified for unknown users (timing safety).
 - Audit log insert-only. All officer writes logged.
 - Input validation on every body (pydantic models or explicit checks);
   reject unknown enums; cap string lengths; reject arrays > 500.

BUILD ORDER FOR THE BACKEND
------------------------------------------------------------------
 1 config, mongo.py, security.py, deps.py, audit.py, init_db.py,
   auth router, /me, change-password, MFA
 2 schemes/templates, public applications (+documents), approve
   (atomic) and direct create, credentials, users
 3 centres, cameras (+reference frame), batches (+generate_sessions),
   trainees (take_seat), staff, equipment, holidays
 4 vision services standalone, tested with a script on sample videos
 5 consolidate + flag_engine unit tests (pure functions)
 6 ledger + sync + files router (evidence, documents)
 7 scheduler + demo runner + staff face enrol/scan + claim
 8 flags router/actions, notices ladder, requests decide, reports,
   accuracy, audit-log
 9 eval script and results.json

TEST CHECKLIST (must pass before demo)
------------------------------------------------------------------
 - Trainer token cannot read another centre's data (403 WRONG_CENTRE).
 - Anonymous request to any non-public route -> 401.
 - 10 parallel POST /my/trainees at (max-1): exactly 1 succeeds, 9 get
   409 ENROLMENT_CAP_REACHED.
 - Double-click Approve: second returns 409 BAD_STATE, exactly one
   centre and one login exist.
 - 5 wrong passwords -> 429 ACCOUNT_LOCKED; works after lock window.
 - Change password: old access and refresh tokens fail; new ones work.
 - Refresh token used twice: second use fails.
 - MFA: wrong code rejected; correct code returns tokens.
 - Expired /files signature -> 403 LINK_EXPIRED.
 - Counts 5,7,10,2 -> not consistent -> more frames -> UNCERTAIN at cap.
 - Counts 6,6,5,6,7 -> consistent, observed 6.
 - Claimed 12 vs observed 7 (ci 6..8) -> A1 flagged. Claimed 7 vs 7 -> none.
 - Black video -> C1 and NO A1.
 - Same video replayed on another day -> C2.
 - Tampered ledger row -> verify_chain returns the bad index.
 - Offline toggle: records queue; after online they sync exactly once.
 - Claim submitted twice without edit_reason -> 409 CLAIM_LOCKED.
 - Staff face enrol without consent -> 422; verify uses own embedding only.
 - Deactivate staff -> staff_faces document is gone.
 - Suspended centre: any centre write -> 403 CENTRE_INACTIVE.
 - Public tracking with wrong phone -> generic 404, no hint.
 - No raw frame file exists anywhere after a run; thumbnails <=60 KB.
 - Mongo shell: TTL indexes exist on evidence_blobs, refresh_tokens,
   camera_health_events, frame_fingerprints.