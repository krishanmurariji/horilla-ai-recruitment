"""
vertex/performance.py

The transparent weighted scoring model, the report generator and the automatic
improvement plan (PIP) trigger, ported from ArielVertex (PerformanceModel,
PerformanceReportService, PipService).

Rules kept from ArielVertex:
* six weighted competency categories; a category marked Not Applicable drops out and its
  weight is shared across the others in proportion;
* a report is built from real evidence in the period (feedback scores, code / project
  reviews, status updates). A category with no evidence gets a neutral 72 and the report says
  so in its data sources;
* the report stays a draft until it is published; publishing a low score starts a PIP.

Improvement over ArielVertex: only feedback that HR has approved (or published) counts as
evidence, so a draft or rejected feedback cannot move someone's score.
"""

import calendar
import logging
import re
from collections import namedtuple
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from vertex.models import (
    CodeReview,
    FeedbackCategoryScore,
    FeedbackStatus,
    Pip,
    PipOutcome,
    PipStatus,
    PerformanceCategoryScore,
    PerformancePeriodType,
    PerformanceRating,
    PerformanceReport,
    ProjectReview,
    StatusUpdate,
    UpdateStatus,
    VertexSetting,
)
from vertex.notify import employees_with_perm, notify_employees, send_plain_email

logger = logging.getLogger(__name__)

Category = namedtuple("Category", "key name factors weight can_be_na")

CATEGORIES = [
    Category("technical", "Technical Competency",
             "Logical thinking, problem-solving, code quality, debugging, architecture, testing awareness", 30, False),
    Category("delivery", "Task Ownership & Delivery",
             "Planning, estimation, timely delivery, requirement understanding, consistency", 20, False),
    Category("collaboration", "Team Collaboration",
             "Communication, participation, knowledge sharing, feedback acceptance, supportiveness", 15, False),
    Category("client", "Client & Stakeholder Communication",
             "Clarity, professional tone, responsiveness, status explanation, requirement clarification", 10, True),
    Category("accountability", "Accountability & Responsibility",
             "Ownership, reliability, follow-through, escalation, attention to detail", 15, False),
    Category("availability", "Availability & Responsiveness",
             "Availability, meeting discipline, blocker communication, status update discipline", 10, False),
]
CATEGORY_BY_KEY = {c.key: c for c in CATEGORIES}

NEUTRAL_SCORE = 72  # used for a category that has no evidence in the period

# (minimum score, rating) from the highest band down
BANDS = [
    (90, PerformanceRating.OUTSTANDING),
    (80, PerformanceRating.EXCEEDS),
    (70, PerformanceRating.MEETS),
    (60, PerformanceRating.NEEDS_IMPROVEMENT),
    (0, PerformanceRating.ATTENTION),
]


def score_colour(score):
    """Bar colour for a 0-100 score, matching the rating bands."""
    if score is None:
        return "#9ca3af"
    if score >= 90:
        return "#15803d"
    if score >= 80:
        return "#16a34a"
    if score >= 70:
        return "#3b82f6"
    if score >= 60:
        return "#f59e0b"
    return "#dc2626"


def rating_for(score):
    for minimum, rating in BANDS:
        if score >= minimum:
            return rating
    return PerformanceRating.ATTENTION


def weighted(scores, not_applicable=()):
    """
    Weighted overall out of 100. ``scores`` maps category key to 0-100; the keys in
    ``not_applicable`` are dropped and their weight is redistributed proportionally.
    """
    active = [c for c in CATEGORIES if c.key not in set(not_applicable)]
    total = sum(c.weight for c in active)
    if not total:
        return Decimal("0.0")
    result = sum(Decimal(scores[c.key]) * Decimal(c.weight) / Decimal(total) for c in active if c.key in scores)
    return result.quantize(Decimal("0.1"))


def effective_weights(not_applicable=()):
    """Percentage each active category really carries once Not Applicable ones are removed."""
    active = [c for c in CATEGORIES if c.key not in set(not_applicable)]
    total = sum(c.weight for c in active) or 1
    return {c.key: round(c.weight * 100 / total, 1) for c in active}


# ---------------------------------------------------------------------------
# Settings (the PIP rule), kept in VertexSetting with defaults
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS = {"pip.enabled": "true", "pip.thresholdPercent": "50"}


def get_setting(key, default=None):
    row = VertexSetting.objects.filter(key=key).first()
    if row is not None:
        return row.value
    return DEFAULT_SETTINGS.get(key, default)


def get_bool(key, default=True):
    value = get_setting(key, str(default))
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def get_int(key, default):
    try:
        return int(get_setting(key, str(default)))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_QUARTER = re.compile(r"^(\d{4})-Q([1-4])$", re.I)


def parse_period(period_type, period):
    """(first day, last day) of "2026-07" (monthly) or "2026-Q3" (quarterly); ValueError if malformed."""
    text = (period or "").strip()
    if period_type == PerformancePeriodType.QUARTERLY:
        match = _QUARTER.match(text)
        if not match:
            raise ValueError(_("Use a quarter like 2026-Q3."))
        year, quarter = int(match.group(1)), int(match.group(2))
        first_month = (quarter - 1) * 3 + 1
        last_month = first_month + 2
        return date(year, first_month, 1), date(year, last_month, calendar.monthrange(year, last_month)[1])
    match = _MONTH.match(text)
    if not match:
        raise ValueError(_("Use a month like 2026-07."))
    year, month = int(match.group(1)), int(match.group(2))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def normalise_period(period_type, period):
    """Canonical text for a period ("2026-q3" becomes "2026-Q3")."""
    parse_period(period_type, period)
    return period.strip().upper() if period_type == PerformancePeriodType.QUARTERLY else period.strip()


def current_quarter(today=None):
    today = today or timezone.localdate()
    return f"{today.year}-Q{(today.month - 1) // 3 + 1}"


def weekdays_between(start, end):
    days, day = 0, start
    while day <= end:
        if day.weekday() < 5:
            days += 1
        day += timedelta(days=1)
    return days


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _scale5(rating):
    return max(1, min(int(rating), 5)) / 5 * 100


def _combine(*signals):
    """Average of the signals that exist; the neutral score when there are none."""
    present = [s for s in signals if s is not None]
    return round(sum(present) / len(present)) if present else NEUTRAL_SCORE


def _top(scores, not_applicable, highest):
    active = {k: v for k, v in scores.items() if k not in not_applicable}
    if not active:
        return ""
    pick = (max if highest else min)(active, key=active.get)
    return CATEGORY_BY_KEY[pick].name


def collect_evidence(subject, period_type, period):
    """Everything the score is derived from, so the generator and the report page agree."""
    start, end = parse_period(period_type, period)
    feedback_scores = list(
        FeedbackCategoryScore.objects.filter(
            feedback__subject=subject,
            feedback__status__in=[FeedbackStatus.APPROVED, FeedbackStatus.PUBLISHED],
        ).filter(
            # the feedback names this period, or was written inside it
            Q(feedback__period__iexact=period)
            | Q(feedback__created_at__date__gte=start, feedback__created_at__date__lte=end)
        )
    )
    in_range = {"submitted_at__date__gte": start, "submitted_at__date__lte": end}
    code_reviews = list(CodeReview.objects.filter(review_request__subject=subject, **in_range))
    project_reviews = list(ProjectReview.objects.filter(review_request__subject=subject, **in_range))
    updates = list(StatusUpdate.objects.filter(employee=subject, update_date__gte=start, update_date__lte=end))
    return {
        "start": start,
        "end": end,
        "feedback_scores": feedback_scores,
        "code_reviews": code_reviews,
        "project_reviews": project_reviews,
        "updates": updates,
    }


def compute_scores(evidence):
    """(scores by category key, set of Not Applicable keys, list of data source labels)."""
    feedback_scores = evidence["feedback_scores"]
    code_reviews = evidence["code_reviews"]
    project_reviews = evidence["project_reviews"]
    updates = evidence["updates"]

    reported_days = len({u.update_date for u in updates})
    expected_days = max(1, weekdays_between(evidence["start"], evidence["end"]) // 2)  # about 2 updates a week
    consistency = min(100.0, reported_days / expected_days * 100)
    blocker_rate = (sum(1 for u in updates if u.status == UpdateStatus.BLOCKED) / len(updates)) if updates else 0

    def feedback_avg(key):
        values = [c.score for c in feedback_scores if c.category == key and not c.not_applicable]
        return sum(values) / len(values) if values else None

    def mean(values):
        values = list(values)
        return sum(values) / len(values) if values else None

    code_avg = mean(
        (_scale5(c.code_quality_rating) + _scale5(c.architecture_rating) + _scale5(c.testing_rating)) / 3
        for c in code_reviews
    )
    delivery_review = mean(_scale5(p.delivery_rating) for p in project_reviews)
    ownership_review = mean(_scale5(p.ownership_rating) for p in project_reviews)
    collaboration_review = mean(_scale5(p.collaboration_rating) for p in project_reviews)

    scores = {
        "technical": _combine(code_avg, feedback_avg("technical")),
        "delivery": _combine(delivery_review, feedback_avg("delivery"), consistency if updates else None),
        "collaboration": _combine(collaboration_review, feedback_avg("collaboration")),
        "accountability": _combine(
            ownership_review, feedback_avg("accountability"), (100 - blocker_rate * 40) if updates else None
        ),
        "availability": _combine(feedback_avg("availability"), consistency if updates else None),
    }

    not_applicable = set()
    client_signal = feedback_avg("client")
    client_marked_na = any(c.category == "client" and c.not_applicable for c in feedback_scores)
    if client_marked_na or client_signal is None:
        not_applicable.add("client")
    else:
        scores["client"] = _combine(client_signal)

    sources = []
    feedback_count = len({c.feedback_id for c in feedback_scores})
    if feedback_count:
        sources.append(_("Feedback (%(n)d)") % {"n": feedback_count})
    if code_reviews:
        sources.append(_("Code review (%(n)d)") % {"n": len(code_reviews)})
    if project_reviews:
        sources.append(_("Project review (%(n)d)") % {"n": len(project_reviews)})
    sources.append(
        _("Status updates (%(a)d/%(b)d expected days)") % {"a": reported_days, "b": expected_days}
    )
    if len(sources) == 1:
        sources.insert(0, _("Limited evidence: neutral baseline applied"))
    return scores, not_applicable, sources


@transaction.atomic
def generate_report(subject, period_type, period):
    """
    Create (or regenerate) the report of one person for one period, as an unpublished draft.
    Regenerating a published report sends it back to draft so it is approved again.
    """
    period = normalise_period(period_type, period)
    evidence = collect_evidence(subject, period_type, period)
    scores, not_applicable, sources = compute_scores(evidence)
    overall = weighted(scores, not_applicable)
    texts = {
        "strengths": _("Strongest in %(c)s, backed by consistent delivery signals.")
        % {"c": _top(scores, not_applicable, True)},
        "improvement_areas": _("Focus area: %(c)s.") % {"c": _top(scores, not_applicable, False)},
        "recommended_actions": _("Set a concrete goal around %(c)s and review progress next cycle.")
        % {"c": _top(scores, not_applicable, False)},
    }
    report, created = PerformanceReport.objects.get_or_create(
        subject=subject, period=period,
        defaults={"period_type": period_type, "overall_score": overall, "rating": rating_for(overall)},
    )
    report.period_type = period_type
    report.overall_score = overall
    report.rating = rating_for(overall)
    report.data_sources = ", ".join(str(s) for s in sources)
    report.is_published = False
    report.published_at = None
    report.approved_by = None
    for field, text in texts.items():
        setattr(report, field, text)
    report.save()
    report.category_scores.all().delete()
    for category in CATEGORIES:
        is_na = category.key in not_applicable
        PerformanceCategoryScore.objects.create(
            report=report, category=category.key,
            score=0 if is_na else scores.get(category.key, NEUTRAL_SCORE),
            weight=category.weight, not_applicable=is_na,
        )
    return report


def category_rows(report):
    """The report's categories with names, scores and the weight each really carried."""
    stored = {c.category: c for c in report.category_scores.all()}
    weights = effective_weights({k for k, c in stored.items() if c.not_applicable})
    rows = []
    for category in CATEGORIES:
        item = stored.get(category.key)
        rows.append(
            {
                "key": category.key,
                "name": category.name,
                "factors": category.factors,
                "score": item.score if item and not item.not_applicable else None,
                "not_applicable": bool(item and item.not_applicable),
                "weight": weights.get(category.key),
                "colour": score_colour(item.score if item and not item.not_applicable else None),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Automatic improvement plan
# ---------------------------------------------------------------------------


def _fmt(value):
    """45.0 -> "45", 47.5 -> "47.5"."""
    return f"{Decimal(str(value)):.1f}".rstrip("0").rstrip(".")


def evaluate_and_trigger_pip(subject, score, period, actor):
    """
    Start a PIP when a published score is under the configured threshold (default 50).
    Skipped when the feature is off, the score is fine, or the person already has an open or
    in-progress plan. Returns the new Pip, or None.
    """
    if not get_bool("pip.enabled", True):
        return None
    threshold = get_int("pip.thresholdPercent", 50)
    score = Decimal(str(score))
    if score >= threshold:
        return None
    if Pip.objects.filter(subject=subject, status__in=[PipStatus.OPEN, PipStatus.IN_PROGRESS]).exists():
        return None
    pip = Pip.objects.create(
        subject=subject,
        is_auto=True,
        trigger_score=score,
        trigger_period=period,
        status=PipStatus.OPEN,
        outcome=PipOutcome.PENDING,
        start_date=timezone.localdate(),
        reason=_("Auto-initiated: %(period)s performance score %(score)s%% is below the %(t)s%% threshold.")
        % {"period": period, "score": _fmt(score), "t": threshold},
        expected_improvement=_("Reach and sustain performance at or above the threshold next cycle."),
        support_provided=_("Manager check-ins and a documented improvement plan."),
        created_by=actor,
    )
    hr = list(employees_with_perm("vertex.add_pip"))
    recipients = [e for e in {*hr, subject}]
    subject_line = _("Performance Improvement Plan: %(name)s") % {"name": subject.get_full_name()}
    notify_employees(actor, recipients, f"{subject_line}. {pip.reason}", reverse("vertex-pip"), icon="alert-circle-outline")
    emails = [e.email for e in recipients if e.email]
    if emails:
        send_plain_email(emails, subject_line, pip.reason, tag=_("Improvement plan"))
    return pip
