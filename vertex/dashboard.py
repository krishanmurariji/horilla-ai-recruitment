"""
vertex/dashboard.py

The Vertex home page for one person: their own cards, what is waiting on them, the health of the
projects they can see and the next calls. Every number is built from the same access rules the
lists use, so the dashboard never shows more than the person could open.
"""

from collections import Counter

from django.db.models import Avg
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from vertex import access
from vertex.models import (
    AppraisalStage,
    FeedbackStatus,
    GoalStatus,
    PipStatus,
    ProjectHealth,
    ProjectStatus,
    PromotionStage,
    TrainingStatus,
)

UPCOMING_CALLS = 5


def _has_any(user, *codenames):
    return any(user.has_perm(f"vertex.{code}") for code in codenames)


def my_cards(user):
    """The person's own numbers; a card appears only when its module is open to them."""
    me = access.employee_of(user)
    cards = []
    if me is None:
        return cards

    if user.has_perm("vertex.view_goal"):
        goals = me.vertex_goals.exclude(status=GoalStatus.CANCELLED)
        average = goals.aggregate(value=Avg("progress"))["value"]
        cards.append(
            {
                "label": _("My goals"),
                "value": f"{round(average)}%" if average is not None else "-",
                "hint": _("%(count)s goal(s)") % {"count": goals.count()},
                "url": reverse("vertex-goals"),
                "icon": "flag-outline",
            }
        )
    if user.has_perm("vertex.view_appraisal"):
        latest = me.vertex_appraisals.select_related("cycle").order_by("-cycle__start_date", "-pk").first()
        stage = AppraisalStage(latest.stage).label if latest else _("No appraisal yet")
        cards.append(
            {
                "label": _("My appraisal"),
                "value": stage,
                "hint": latest.cycle.name if latest else "",
                "url": reverse("vertex-appraisals"),
                "icon": "ribbon-outline",
                "small": True,
            }
        )
    if user.has_perm("vertex.view_myperformance"):
        published = access.my_published_feedback(user).count()
        cards.append(
            {
                "label": _("My feedback"),
                "value": published,
                "hint": _("published"),
                "url": reverse("vertex-my-performance"),
                "icon": "chatbubbles-outline",
            }
        )
        active_plans = access.my_pips(user).filter(status__in=(PipStatus.OPEN, PipStatus.IN_PROGRESS)).count()
        cards.append(
            {
                "label": _("My improvement plans"),
                "value": active_plans,
                "hint": _("active"),
                "url": reverse("vertex-my-performance"),
                "icon": "trending-up-outline",
                "alert": bool(active_plans),
            }
        )
    if user.has_perm("vertex.view_trainingrecommendation"):
        open_training = access.my_training(user).exclude(status=TrainingStatus.COMPLETED).count()
        cards.append(
            {
                "label": _("My training"),
                "value": open_training,
                "hint": _("open"),
                "url": reverse("vertex-training"),
                "icon": "school-outline",
            }
        )
    return cards


def pending_actions(user):
    """What is waiting on this person; rows with nothing waiting are left out."""
    rows = []

    def add(label, count, url_name):
        if count:
            rows.append({"label": label, "count": count, "url": reverse(url_name)})

    me = access.employee_of(user)

    if access.can_approve_feedback(user):
        add(
            _("Feedback waiting for approval"),
            access.visible_feedback(user).filter(status=FeedbackStatus.SUBMITTED).count(),
            "vertex-feedback",
        )
    if user.has_perm("vertex.view_appraisal"):
        appraisals = list(access.visible_appraisals(user).exclude(stage=AppraisalStage.RELEASED))
        add(_("Self assessments to complete"), sum(access.can_self_assess(user, a) for a in appraisals), "vertex-appraisals")
        add(_("Appraisals to evaluate"), sum(access.can_evaluate(user, a) for a in appraisals), "vertex-appraisals")
        add(_("Appraisals to release"), sum(access.can_release(user, a) for a in appraisals), "vertex-appraisals")
    if user.has_perm("vertex.view_promotion"):
        promotions = list(
            access.visible_promotions(user).exclude(stage__in=(PromotionStage.COMPLETED, PromotionStage.REJECTED))
        )
        add(_("Promotions to validate"), sum(access.can_validate(user, p) for p in promotions), "vertex-promotions")
        add(_("Promotions to approve"), sum(access.can_approve(user, p) for p in promotions), "vertex-promotions")
        add(_("Promotions to complete"), sum(access.can_complete(user, p) for p in promotions), "vertex-promotions")
    if user.has_perm("vertex.view_expense"):
        expenses = list(access.visible_expenses(user).exclude(status__in=("paid", "rejected")))
        add(_("Expenses to approve"), sum(access.can_approve_expense(user, e) for e in expenses), "vertex-expenses")
        add(_("Expenses to pay"), sum(access.can_pay_expense(user, e) for e in expenses), "vertex-expenses")
    if me is not None and user.has_perm("vertex.view_pip"):
        add(
            _("Improvement plans that need you"),
            access.my_pips(user).filter(status__in=(PipStatus.OPEN, PipStatus.IN_PROGRESS)).count(),
            "vertex-my-performance",
        )
    return rows


def project_health(user):
    """Counts by status and by health over the projects this person can open."""
    projects = list(access.visible_projects(user).values_list("status", "health"))
    by_status = Counter(status for status, _health in projects)
    by_health = Counter(health for _status, health in projects)
    return {
        "total": len(projects),
        "status": [{"label": label, "count": by_status.get(value, 0), "value": value} for value, label in ProjectStatus.choices],
        "health": [{"label": label, "count": by_health.get(value, 0), "value": value} for value, label in ProjectHealth.choices],
    }


def upcoming_calls(user):
    from vertex.models import ProjectCall

    return list(
        ProjectCall.objects.filter(
            project__in=access.visible_projects(user).values("pk"), scheduled_at__gte=timezone.now()
        )
        .select_related("project")
        .order_by("scheduled_at")[:UPCOMING_CALLS]
    )


def dashboard_for(user):
    return {
        "cards": my_cards(user),
        "pending": pending_actions(user),
        "health": project_health(user) if user.has_perm("vertex.view_project") else None,
        "calls": upcoming_calls(user) if user.has_perm("vertex.view_project") else [],
    }
