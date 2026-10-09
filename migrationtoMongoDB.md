==================================================================
 MIGRATION: SUPABASE -> MONGODB ATLAS  (read first, overrides older text)
==================================================================

1. ARCHITECTURE NOW
------------------------------------------------------------------
 [Gov app] [Centre app]  --HTTPS (ngrok)-->  [FastAPI backend = edge node]
   (only NEXT_PUBLIC_API_URL)                   |  PyMongo -> MongoDB Atlas (ONE cluster, db "devskill")
                                                |  local SQLite ledger (offline buffer, unchanged)
 - Frontends hold NO database or auth-provider keys at all.
 - Backend alone knows MONGODB_URI, JWT_SECRET, FERNET_KEY, FILE_URL_SECRET.
 - Atlas Database User: role readWrite on db "devskill" only (no admin).
 - Atlas Network Access: laptop IP changes, so for the demo allow
   0.0.0.0/0 and REMOVE it after SIH. Password in the URI must be
   URL-encoded. Use the mongodb+srv:// string and pymongo[srv].

2. WHAT IS REMOVED
------------------------------------------------------------------
 supabase python package, supabase-js, @supabase/*, RLS, SQL functions,
 triggers, views, Supabase Storage buckets, lib/supabase.ts,
 env vars SUPABASE_* and NEXT_PUBLIC_SUPABASE_*.

3. NEW ENV (backend .env)
------------------------------------------------------------------
 MONGODB_URI=mongodb+srv://<user>:<urlencoded-pass>@<cluster>.mongodb.net/?retryWrites=true&w=majority
 MONGODB_DB=devskill
 JWT_SECRET=<64+ random chars>        # python -c "import secrets;print(secrets.token_urlsafe(64))"
 FILE_URL_SECRET=<another random 64>
 FERNET_KEY=...  EDGE_SIGNING_KEY_HEX=...  DEVICE_ID=edge-001
 ALLOWED_ORIGINS=...  FORCE_OFFLINE=false
 requirements.txt: REMOVE supabase. ADD pymongo[srv], PyJWT, argon2-cffi,
 pyotp, slowapi. Frontends .env.local: ONLY NEXT_PUBLIC_API_URL.
 Update config.py: mongodb_uri, mongodb_db, jwt_secret, file_url_secret.

4. DATA CONVENTIONS (builder must follow exactly)
------------------------------------------------------------------
 - _id is ObjectId internally; the API always returns it as string "id"
   (use ser() in app/db/mongo.py). Parse incoming ids with oid() (422 if bad).
 - All foreign references are ObjectId fields (centre_id, batch_id...).
 - Timestamps: timezone-aware UTC datetimes (client created with tz_aware=True).
 - Date-only values are ISO strings "YYYY-MM-DD" (session_date,
   holiday_date, start_date, end_date, expected_fix_date, approved_until).
   Time-of-day values are "HH:MM" strings (batches.start_time/end_time).
 - No foreign keys or cascades: delete/deactivate logic is done in code
   (e.g. deactivating staff deletes their staff_faces doc).
 - No triggers: enrolment cap uses an atomic counter (centres.enrolled_count).
 - Never use find().skip() for big lists; paginate with limit + _id cursor.
 - audit_log is insert-only. Never update or delete it in code.

5. TABLE -> COLLECTION MAP
------------------------------------------------------------------
 auth.users + profiles   -> users        (login_id unique, password_hash,
                                          role, centre_id, must_change_password,
                                          temp_password_expires_at, active,
                                          token_version, failed_attempts,
                                          locked_until, mfa_enabled, mfa_secret_enc)
 refresh_tokens (new)    -> refresh_tokens (token_hash unique, TTL on expires_at)
 schemes, trade_templates, applications, centres, staff, batches,
 holidays, trainees, sessions, attendance_claims, staff_attendance,
 cameras, edge_devices, ledger_entries, observations, notices,
 change_requests, repair_declarations, audit_log -> same names
 centre_equipment        -> EMBEDDED: centres.equipment[]
                            [{class,label,sanctioned_qty,declared_qty}]
 application_events      -> EMBEDDED: applications.events[]
 flag_events             -> EMBEDDED: flags.events[]
 flags.evidence_paths    -> flags.evidence_ids[]  (strings, ids of evidence_blobs)
 applications.documents  -> [{name,file_id,size,content_type}] (GridFS ids)
 staff.profile_id        -> staff.user_id
 staff_faces             -> staff_faces (_id = staff _id; embedding_enc = Binary)
 camera_health_events, frame_fingerprints -> collections with TTL indexes
 evidence_items          -> evidence_blobs (thumbnail bytes + meta, TTL 90 days)
 sequences/functions     -> counters collection + Python (see M4)
 views                   -> aggregation pipelines in Python (see M4)
 Supabase Storage        -> evidence_blobs (<=60 KB each) + GridFS "documents"

6. KEY BEHAVIOUR CHANGES
------------------------------------------------------------------
 - Login: POST /auth/login {login_id, password}. Govt login_id = email.
   Centre login_id = DSK-WB-0001 or DSK-WB-0001-T01 (case-insensitive).
 - Tokens: access JWT (30 min) + opaque refresh token (7 days, rotated,
   stored hashed). Password change bumps users.token_version, which
   invalidates all old tokens.
 - Evidence/document images are served by the backend at
   /files/<kind>/<id>?exp=&sig= (HMAC, 60 s). Because ngrok free shows an
   interstitial for plain <img> requests, the frontend loads images with
   fetch() + header "ngrok-skip-browser-warning" -> blob URL (see M5).
 - Edge device row is upserted at backend startup (no manual SQL).
 - Atlas free-tier storage is small (512 MB): keep thumbnails <=60 KB,
   rely on the TTL indexes, never store raw frames.
 - Limitation text update: login and claims need Atlas (internet);
   capture, pipeline, ledger keep working offline via SQLite.