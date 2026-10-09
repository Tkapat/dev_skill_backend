from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.routers import applications, attendance, auth, files, trainees
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

for r in (applications.router, trainees.router, attendance.router, auth.router, files.router):
    app.include_router(r)


@app.on_event("startup")
def _start():
    register_device()
    scheduler.start()


@app.get("/health")
def health():
    return {"ok": True}
