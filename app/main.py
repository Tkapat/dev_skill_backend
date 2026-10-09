from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.limiter import limiter, rate_limit_exceeded_handler
from app.routers import (
    applications,
    attendance,
    auth,
    cameras,
    edge,
    equipment,
    files,
    flags,
    notices,
    reports,
    requests,
    schedule,
    schemes,
    staff,
    trainees,
    users,
)
from app.services import scheduler
from app.services.sync import register_device

app = FastAPI(title="Dev_Skill API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.state.limiter = limiter
app.add_exception_handler(429, rate_limit_exceeded_handler)

routers = [
    applications.router,
    attendance.router,
    auth.router,
    cameras.router,
    edge.router,
    equipment.router,
    files.router,
    flags.router,
    notices.router,
    reports.router,
    requests.router,
    schedule.router,
    schemes.router,
    staff.router,
    trainees.router,
    users.router,
]
for r in routers:
    app.include_router(r)


@app.on_event("startup")
def _start():
    register_device()
    scheduler.start()


@app.get("/health")
def health():
    return {"ok": True}
