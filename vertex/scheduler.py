"""
vertex/scheduler.py

Background jobs of Ariel Vertex, on the APScheduler pattern the other Ariel HR modules use
(see payroll_pro/scheduler.py). Every 15 minutes one tick sends the daily expense summary after 18:00
and the weekly one on Fridays after 18:00 (vertex.expenses), and one tick starts the status reminders,
the quarterly feedback requests and the monthly reports when they are due (vertex.jobs). Each makes
sure it runs once for its day, quarter or month.
"""

import logging
import sys

from apscheduler.schedulers.background import BackgroundScheduler

from horilla.background_utils import with_db_cleanup

logger = logging.getLogger(__name__)


@with_db_cleanup
def automatic_jobs_tick():
    from vertex import jobs

    try:
        ran = jobs.run_due()
        if ran:
            logger.info("Vertex jobs run: %s", ran)
    except Exception:
        logger.exception("Vertex automatic jobs failed")


@with_db_cleanup
def expense_summaries_tick():
    from vertex import expenses

    try:
        sent = expenses.run_due_summaries()
        if sent:
            logger.info("Vertex expense summaries sent: %s", ", ".join(sent))
    except Exception:
        logger.exception("Vertex expense summary job failed")


if not any(
    cmd in sys.argv
    for cmd in [
        "makemigrations",
        "migrate",
        "compilemessages",
        "flush",
        "shell",
        "collectstatic",
        "test",
        "vertex_demo_users",
        "vertex_expense_summary",
        "vertex_run_jobs",
    ]
):
    scheduler = BackgroundScheduler()
    scheduler.add_job(expense_summaries_tick, "interval", minutes=15)
    scheduler.add_job(automatic_jobs_tick, "interval", minutes=15)
    scheduler.start()
