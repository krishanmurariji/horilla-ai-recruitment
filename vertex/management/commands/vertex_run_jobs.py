"""
Run the Vertex automatic jobs by hand.

    python manage.py vertex_run_jobs                 # whatever is due now (what the scheduler does)
    python manage.py vertex_run_jobs status_reminders --now
    python manage.py vertex_run_jobs quarterly_feedback --now
    python manage.py vertex_run_jobs monthly_reports --now

Without --now only jobs that are due and not yet run for their day, quarter or month are started.
With --now the named job runs straight away, whatever the date.
"""

from django.core.management.base import BaseCommand, CommandError

from vertex import jobs


class Command(BaseCommand):
    help = "Run the Vertex status reminders, quarterly feedback requests and monthly reports."

    def add_arguments(self, parser):
        parser.add_argument("job", nargs="?", choices=sorted(jobs.JOBS))
        parser.add_argument("--now", action="store_true", help="run the named job now, ignoring its window")

    def handle(self, *args, **options):
        if options["job"] and options["now"]:
            self.stdout.write(self.style.SUCCESS(f"{options['job']}: {jobs.run_now(options['job'])}"))
            return
        if options["job"]:
            raise CommandError("Add --now to run a named job outside its usual window.")
        ran = jobs.run_due()
        self.stdout.write(self.style.SUCCESS(f"ran: {ran}" if ran else "nothing is due"))
