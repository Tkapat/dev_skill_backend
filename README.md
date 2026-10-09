# Dev_Skill Backend

FastAPI backend for the AI Video-Analytics Compliance Monitor for Skilling Centres (SIH 2026).

## Tech Stack
- **API**: FastAPI + Uvicorn
- **Database**: MongoDB Atlas (PyMongo)
- **Auth**: JWT (HS256) + Argon2 password hashing + TOTP MFA
- **Vision**: Ultralytics YOLO / YOLO-World, OpenCV, InsightFace
- **Integrity**: Local SQLite ledger (SHA-256 hash chain + Ed25519 signatures)
- **Sync**: Store-and-forward, idempotent, flags prioritized
- **Scheduler**: APScheduler (sync tick, session generation, repair expiry)

## Project Structure
```
dev_skill_backend/
├── app/
│   ├── core/           # config, security, deps, audit
│   ├── db/             # mongo.py (client, helpers)
│   ├── routers/        # auth, files, applications, trainees, attendance, ...
│   ├── services/       # credentials, vision, pipeline, flag_engine, ledger, sync, face, scheduler, dbfuncs
│   └── main.py
├── scripts/
│   └── init_db.py      # Run once to create collections, indexes, seed data
├── sql/                # (Legacy - kept for reference)
├── data/               # Local SQLite ledger + evidence temp (gitignored)
├── sample_videos/      # Demo videos (gitignored or add via LFS)
├── eval/               # Evaluation scripts + results.json
├── models/             # YOLO weights (auto-downloaded)
├── requirements.txt
├── .env.example
└── README.md
```

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your values (see .env.example for required variables)
```

### 3. Initialize database (run once)
```bash
python -m scripts.init_db
# Prompts for super admin email/password
```

### 4. Run development server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Expose via ngrok (for frontend integration)
```bash
ngrok http 8000
# Update NEXT_PUBLIC_API_URL in frontend .env with the ngrok HTTPS URL
```

## Key Endpoints

| Area | Endpoints |
|------|-----------|
| **Auth** | `POST /auth/login`, `POST /auth/mfa/login`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me`, `POST /me/change-password`, `POST /me/mfa/setup`, `POST /me/mfa/enable` |
| **Files** | `GET /files/evidence/{id}?exp=...&sig=...`, `GET /files/document/{id}?exp=...&sig=...`, `POST /public/applications/{code}/documents` |
| **Applications** | `POST /public/applications`, `GET /public/applications/track`, `PUT /public/applications/{code}`, `GET /public/schemes`, `POST /applications/{id}/approve`, `POST /applications/{id}/reject`, `POST /applications/{id}/query`, `GET /applications`, `GET /applications/{id}`, `POST /centres` (govt direct) |
| **Centres** | `GET /centres`, `GET /centres/{id}`, `POST /centres/{id}/reset-credentials` |
| **Trainees** | `POST /my/trainees`, `GET /my/trainees`, `POST /my/trainees/{id}/deactivate` |
| **Attendance** | `POST /my/sessions/{id}/claim`, `POST /my/sessions/{id}/staff-scan` |
| **Edge/Demo** | `POST /edge/demo/run`, `POST /edge/offline`, `POST /edge/sync-now`, `GET /edge/status` |
| **System** | `GET /health` |

## Auth Flow
1. Frontend calls `POST /auth/login` with `login_id` + `password`
2. If govt role + MFA enabled → returns `{mfa_required: true, mfa_token: ...}`
3. Frontend calls `POST /auth/mfa/login` with `mfa_token` + `code`
4. On success: returns `{access_token, refresh_token, user}`
5. Access token: 30 min, JWT HS256, contains `sub`, `tv` (token_version), `typ: "access"`
6. Refresh token: 7 days, stored hashed in `refresh_tokens` collection, **rotation on use**
7. Change password / logout / MFA enable → increments `token_version` → invalidates all existing tokens

## Database Collections (auto-created by `init_db.py`)
- `users` - All profiles (govt + centre + trainer)
- `centres` - Skilling centres with embedded equipment
- `schemes` / `trade_templates` - Govt-defined, drive detector classes
- `applications` - Public + govt direct creation
- `staff` / `staff_faces` - Trainers/officials, encrypted embeddings only
- `batches` / `holidays` / `trainees` - Centre master data
- `sessions` / `attendance_claims` / `staff_attendance` - Daily ops
- `cameras` / `camera_health_events` / `frame_fingerprints` - Camera mgmt
- `edge_devices` / `ledger_entries` / `observations` / `flags` - Edge integrity
- `evidence_blobs` - Blurred thumbnails (GridFS alternative)
- `repair_declarations` / `change_requests` / `notices` - Flag handling
- `audit_log` - Every officer action
- `refresh_tokens` - Rotating refresh tokens (TTL index)
- `counters` - Sequence generators

## Offline Mode
- All inference runs locally, no video streamed
- Set `FORCE_OFFLINE=true` in `.env` or `POST /edge/offline {"value": true}`
- Ledger writes to local SQLite (`data/edge.db`)
- `POST /edge/sync-now` or background scheduler pushes to MongoDB when online
- Idempotent via unique `ledger_hash` (SHA-256) — re-sync never duplicates

## Vision Pipeline (services/vision/)
- `detector.py` - YOLO (COCO classes) or YOLO-World (open-vocab), 2x2 tiling + NMS
- `quality.py` - Dark/covered/blurry/frozen/moved detection + dhash for replay (C2)
- `cleanup.py` - ROI filtering, presence rules (seated/workstation/any), staff subtraction
- `privacy.py` - Head-region blur (top 28%), annotated boxes, 640px JPEG q60
- `equipment.py` - Operability: `screen_on` (brightness/var), `in_use` (person overlap)
- `pipeline.py` - Hourly capture loop with quality gate, retakes, consolidation
- `consolidate.py` - Consensus count with tolerance + agree_ratio

## Flag Engine (services/flag_engine.py)
Compares **Sanctioned** (template) vs **Claimed** (roster) vs **Observed** (camera):
- A1 Attendance inflation, A2 Over capacity, A3 Enrolment mismatch, A4 Ghost session, A5 Unregistered attendees
- E1 Equipment missing, E2 Non-operational, E3 Declaration mismatch
- S1 Trainer not verified, C1 Camera issue, C2 Replay, C3 Missed captures
- D1 Data integrity, U1 Unverifiable count, P1 Plausibility (approval time)

## Evaluation
```bash
cd eval
python evaluate.py
# Outputs results.json (read by GET /accuracy)
```

## Deployment Notes
- Frontend (Next.js) on Vercel → calls this API via `NEXT_PUBLIC_API_URL`
- Backend runs on edge device (laptop) exposed via ngrok
- MongoDB Atlas: ensure IP allowlist includes ngrok IPs (or use VPC peering)
- Supabase **not used** — all auth/storage in FastAPI + MongoDB
- Evidence retention: TTL index on `evidence_blobs.created_at` (90 days)

## License
Internal SIH 2026 project.