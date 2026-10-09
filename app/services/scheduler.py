from apscheduler.schedulers.background import BackgroundScheduler
from app.services.sync import sync_tick

sched = BackgroundScheduler(timezone="UTC")


def start():
    sched.add_job(sync_tick, "interval", seconds=20, max_instances=1, id="sync")
    # sched.add_job(capture_tick, "interval", seconds=30, max_instances=1)  # live cameras:
    #   for each LIVE centre with active session: if now >= next planned random time -> analyse
    #   planned times = random.sample(range(window_seconds), k) generated when session opens
    sched.start()