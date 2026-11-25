"""
vertex/jobs.py

The automatic jobs of ArielVertex, on Ariel HR's scheduler (see vertex/scheduler.py):

* status reminders  - weekdays after 16:00: whoever has not sent today's status update is reminded,
                      and each project manager is told how many people are missing on their project;
* quarterly feedback - in the last week of March, June, September and December: the reviewer of each
                      developer / QA person (project manager, else reporting manager) is asked for the
                      quarter's feedback when none exists yet;
* monthly reports   - in the first days of a month: a draft performance report of last month for every
                      developer / QA person, and HR is told they are ready to review.

(The daily and weekly expense summaries are in vertex/expenses.py.)

Every job claims its slot in ``VertexSetting`` before it runs, so with several server processes, or a
restart in the middle of the evening, it still runs once for that day, quarter or month.
"""

import logging
from datetime import date, timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _t

from vertex import performance
from vertex.models import (
    Feedback,
    PerformancePeriodType,
    PerformanceReport,
    ProjectMember,
    ProjectRole,
    ProjectStatus,
    StatusUpdate,
)
from vertex.notify import employees_with_perm, notify_employees

logger = logging.getLogger(__name__)

STATUS_REMINDER_FROM_HOUR = 16
QUARTER_END_MONTHS = (3, 6, 9, 12)
QUARTER_WINDOW_FROM_DAY = 24
MONTHLY_REPORT_UNTIL_DAY = 3
REPORTED_ROLES = (ProjectRole.DEVELOPER, ProjectRole.QA)
LEAD_ROLES = (ProjectRole.PROJECT_MANAGER, ProjectRole.PROJECT_COORDINATOR)


def claim(key, token):
    """True for exactly one caller per ``token`` (a date, a quarter, a month), however many processes run."""
    from vertex.models import VertexSetting

    VertexSetting.objects.get_or_create(key=key, defaults={"value": ""})
    return VertexSetting.objects.filter(key=key).exclude(value=str(token)).update(value=str(token)) == 1


def _working_members(today):
    """Active developer / QA memberships on active projects, still running today."""
    return (
        ProjectMember.objects.filter(
            is_active=True, project__status=ProjectStatus.ACTIVE, role_on_project__in=REPORTED_ROLES,
            employee__is_active=True,
        )
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today))
        .select_related("project", "employee")
    )


def status_reminders(today=None):
    """Remind everyone who has not sent today's update; tell each project lead how many are missing."""
    today = today or timezone.localdate()
    members = list(_working_members(today))
    reported = set(
        StatusUpdate.objects.filter(update_date__gte=today).values_list("project_id", "employee_id")
    )
    missing = [m for m in members if (m.project_id, m.employee_id) not in reported]
    reminded = 0
    for member in missing:
        reminded += notify_employees(
            None, [member.employee],
            _t("You have not sent today's status update for %(project)s.") % {"project": member.project.name},
            reverse("vertex-status"), icon="alarm-outline",
        )
    by_project = {}
    for member in missing:
        by_project.setdefault(member.project, []).append(member)
    told = 0
    for project, absent in by_project.items():
        leads = [
            m.employee for m in ProjectMember.objects.filter(
                project=project, is_active=True, role_on_project__in=LEAD_ROLES
            ).select_related("employee")
        ]
        told += notify_employees(
            None, leads,
            _t("%(n)s of your team have not sent today's status update on %(project)s.")
            % {"n": len(absent), "project": project.name},
            reverse("vertex-status"), icon="alarm-outline",
        )
    return {"reminded": reminded, "leads_told": told}


def current_quarter(today):
    return f"{today.year}-Q{(today.month - 1) // 3 + 1}"


def quarterly_feedback_requests(today=None):
    """Ask each reviewer for the quarter's feedback on the people they review, when none exists yet."""
    today = today or timezone.localdate()
    quarter = current_quarter(today)
    done = set(Feedback.objects.filter(period=quarter).values_list("subject_id", flat=True))
    leads = {}
    for member in ProjectMember.objects.filter(
        is_active=True, project__status=ProjectStatus.ACTIVE, role_on_project__in=LEAD_ROLES
    ).select_related("employee"):
        leads.setdefault(member.project_id, member.employee)
    wanted = {}  # reviewer pk -> (reviewer, {subject pk: subject})
    for member in _working_members(today):
        subject = member.employee
        if subject.pk in done:
            continue
        reviewer = leads.get(member.project_id) or subject.get_reporting_manager()
        if reviewer is None or reviewer.pk == subject.pk:
            continue
        wanted.setdefault(reviewer.pk, (reviewer, {}))[1][subject.pk] = subject
    asked = 0
    for reviewer, subjects in wanted.values():
        names = ", ".join(sorted(s.get_full_name() for s in subjects.values()))
        asked += notify_employees(
            None, [reviewer],
            _t("Feedback for %(quarter)s is due for: %(names)s.") % {"quarter": quarter, "names": names},
            reverse("vertex-feedback"), icon="chatbubbles-outline",
        )
    return {"quarter": quarter, "reviewers_asked": asked, "people": sum(len(s) for _r, s in wanted.values())}


def previous_month(today):
    first = today.replace(day=1)
    last = first - timedelta(days=1)
    return f"{last.year:04d}-{last.month:02d}"


def monthly_reports(today=None):
    """A draft report of last month for each developer / QA person who has none yet; HR is told."""
    today = today or timezone.localdate()
    period = previous_month(today)
    subjects = {m.employee_id: m.employee for m in _working_members(today)}
    have = set(PerformanceReport.objects.filter(period=period).values_list("subject_id", flat=True))
    generated = 0
    for pk, subject in subjects.items():
        if pk in have:
            continue  # never overwrite a report someone already reviewed or published
        try:
            performance.generate_report(subject, PerformancePeriodType.MONTHLY, period)
            generated += 1
        except Exception:
            logger.exception("Vertex monthly report failed for %s", subject)
    if generated:
        notify_employees(
            None, list(employees_with_perm("vertex.change_performancepublication")),
            _t("%(n)s draft performance report(s) for %(period)s are ready to review and publish.")
            % {"n": generated, "period": period},
            reverse("vertex-performance"), icon="analytics-outline",
        )
    return {"period": period, "generated": generated}


def _due_status(now):
    return now.weekday() < 5 and now.hour >= STATUS_REMINDER_FROM_HOUR


def _due_feedback(now):
    return now.month in QUARTER_END_MONTHS and now.day >= QUARTER_WINDOW_FROM_DAY and now.hour >= 9


def _due_reports(now):
    return now.day <= MONTHLY_REPORT_UNTIL_DAY and now.hour >= 7


# name -> (when it is due, the claim token for that slot, the job)
JOBS = {
    "status_reminders": (_due_status, lambda now: now.date().isoformat(), lambda now: status_reminders(now.date())),
    "quarterly_feedback": (_due_feedback, lambda now: current_quarter(now.date()), lambda now: quarterly_feedback_requests(now.date())),
    "monthly_reports": (_due_reports, lambda now: previous_month(now.date()), lambda now: monthly_reports(now.date())),
}


def run_due(now=None):
    """The scheduler tick: run each job that is due and not yet run for its day, quarter or month."""
    now = timezone.localtime(now or timezone.now())
    ran = {}
    for name, (is_due, token, job) in JOBS.items():
        if is_due(now) and claim(f"jobs.{name}", token(now)):
            try:
                ran[name] = job(now)
            except Exception:
                logger.exception("Vertex job %s failed", name)
    return ran


def run_now(name, today=None):
    """Run one job straight away, ignoring its window (the manual command and the tests use this)."""
    today = today or timezone.localdate()
    now = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    if name not in JOBS:
        raise KeyError(name)
    return JOBS[name][2](now)
