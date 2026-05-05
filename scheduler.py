"""Scheduler — manages cron jobs for IDX and US pipelines inside Docker."""

import logging
import signal
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "data" / "scheduler.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scheduler")


def job_idx_fetch():
    """IDX: fetch all pool data + compute indicators/signals."""
    log.info("Starting IDX fetch pipeline...")
    from run_eod import run
    run(market="idx", step="fetch")


def job_idx_report():
    """IDX: assemble report + send Telegram brief."""
    log.info("Starting IDX report...")
    from run_eod import run
    run(market="idx", step="report", notify=True)


def job_us_fetch():
    """US: fetch prices + compute indicators/signals."""
    log.info("Starting US fetch pipeline...")
    from run_eod import run
    run(market="us", step="fetch")


def job_us_report():
    """US: assemble scanner report + send Telegram brief."""
    log.info("Starting US report...")
    from run_eod import run
    run(market="us", step="report", notify=True)


def main():
    scheduler = BlockingScheduler(timezone="Asia/Jakarta")

    # IDX: fetch after market close (16:15 WIB), report before open (07:30 WIB)
    scheduler.add_job(job_idx_fetch, CronTrigger(hour=16, minute=15, day_of_week="mon-fri"),
                      id="idx_fetch", name="IDX fetch + compute")
    scheduler.add_job(job_idx_report, CronTrigger(hour=7, minute=30, day_of_week="mon-fri"),
                      id="idx_report", name="IDX morning brief")

    # US: fetch after market close (05:30 WIB Tue-Sat), report before open (20:00 WIB Mon-Fri)
    scheduler.add_job(job_us_fetch, CronTrigger(hour=5, minute=30, day_of_week="tue-sat"),
                      id="us_fetch", name="US fetch + compute")
    scheduler.add_job(job_us_report, CronTrigger(hour=20, minute=0, day_of_week="mon-fri"),
                      id="us_report", name="US evening brief")

    # Graceful shutdown
    def shutdown(signum, frame):
        log.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    log.info("Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        log.info("  %s — %s", job.name, job.trigger)

    scheduler.start()


if __name__ == "__main__":
    main()
