"""
vertex/views_performance.py

Feedback workflow (draft -> submitted -> approved -> published), the performance report page and
the employee's own "My Performance" page. Lists and modal forms are in vertex/cbv_performance.py.

Rules ported from ArielVertex:
* nothing is employee-visible until HR has approved AND published it;
* the internal notes never reach the employee;
* publishing a low score starts an improvement plan.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access, performance
from vertex.forms_performance import RevisionForm
from vertex.models import FeedbackStatus
from vertex.notify import employees_with_perm, notify_employees


def _feedback_or_404(request, pk):
    feedback = access.visible_feedback(request.user).filter(pk=pk).first()
    if feedback is None:
        raise Http404
    return feedback


def _score_rows(feedback):
    stored = {c.category: c for c in feedback.category_scores.all()}
    rows = []
    for category in performance.CATEGORIES:
        item = stored.get(category.key)
        rows.append(
            {
                "name": category.name,
                "factors": category.factors,
                "score": item.score if item and not item.not_applicable else None,
                "not_applicable": bool(item and item.not_applicable),
                "comment": item.comment if item else "",
                "missing": item is None,
                "colour": performance.score_colour(item.score if item and not item.not_applicable else None),
            }
        )
    return rows


def missing_scores(feedback):
    """Names of the competencies that have neither a score nor a Not Applicable mark yet."""
    done = set(feedback.category_scores.values_list("category", flat=True))
    return [c.name for c in performance.CATEGORIES if c.key not in done]


# ---------------------------------------------------------------------------
# Feedback
# ---------------------------------------------------------------------------


@login_required
@permission_required("vertex.view_feedback")
def feedback_detail(request, pk):
    feedback = _feedback_or_404(request, pk)
    user = request.user
    can_approve = access.can_approve_feedback(user)
    return render(
        request,
        "vertex/feedback_detail.html",
        {
            "feedback": feedback,
            "rows": _score_rows(feedback),
            "average": feedback.get_average_score(),
            "can_edit": access.can_edit_feedback(user, feedback),
            "can_approve": can_approve,
            "can_request_revision": can_approve and feedback.status == FeedbackStatus.SUBMITTED,
            "can_approve_now": can_approve
            and feedback.status in (FeedbackStatus.SUBMITTED, FeedbackStatus.REVISION_REQUESTED),
            "can_publish": can_approve and feedback.status == FeedbackStatus.APPROVED,
            "revision_form": RevisionForm(),
            "statuses": FeedbackStatus,
        },
    )


def _notify_approvers(request, feedback):
    notify_employees(
        access.employee_of(request.user),
        employees_with_perm("vertex.change_feedbackapproval"),
        str(_("Feedback awaiting approval: %(who)s (%(period)s).")) % {
            "who": feedback.subject.get_full_name(), "period": feedback.period,
        },
        feedback.get_absolute_url(),
        icon="chatbubble-ellipses-outline",
    )


@login_required
@permission_required("vertex.change_feedback")
@post_only
def feedback_submit(request, pk):
    feedback = _feedback_or_404(request, pk)
    if not access.can_edit_feedback(request.user, feedback):
        return handle_no_permission(request)
    missing = missing_scores(feedback)
    if missing:
        messages.error(
            request,
            _("Score every applicable competency before submitting. Missing: %(names)s.")
            % {"names": ", ".join(missing)},
        )
        return redirect(feedback.get_absolute_url())
    feedback.status = FeedbackStatus.SUBMITTED
    feedback.revision_reason = ""
    feedback.save(update_fields=["status", "revision_reason", "updated_at"])
    _notify_approvers(request, feedback)
    messages.success(request, _("Feedback submitted for HR approval."))
    return redirect(feedback.get_absolute_url())


@login_required
@permission_required("vertex.change_feedbackapproval")
@post_only
def feedback_request_revision(request, pk):
    feedback = _feedback_or_404(request, pk)
    if feedback.status != FeedbackStatus.SUBMITTED:
        messages.error(request, _("Only submitted feedback can be sent back."))
        return redirect(feedback.get_absolute_url())
    form = RevisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(e for errs in form.errors.values() for e in errs))
        return redirect(feedback.get_absolute_url())
    feedback.status = FeedbackStatus.REVISION_REQUESTED
    feedback.revision_reason = form.cleaned_data["reason"]
    feedback.save(update_fields=["status", "revision_reason", "updated_at"])
    notify_employees(
        access.employee_of(request.user),
        [feedback.author],
        str(_("Feedback revision requested for %(who)s: %(reason)s"))
        % {"who": feedback.subject.get_full_name(), "reason": feedback.revision_reason},
        feedback.get_absolute_url(),
    )
    messages.success(request, _("Sent back to the author for revision."))
    return redirect(feedback.get_absolute_url())


@login_required
@permission_required("vertex.change_feedbackapproval")
@post_only
def feedback_approve(request, pk):
    feedback = _feedback_or_404(request, pk)
    if feedback.status not in (FeedbackStatus.SUBMITTED, FeedbackStatus.REVISION_REQUESTED):
        messages.error(request, _("Only submitted feedback can be approved."))
        return redirect(feedback.get_absolute_url())
    feedback.status = FeedbackStatus.APPROVED
    feedback.approved_by = access.employee_of(request.user)
    feedback.approved_at = timezone.now()
    feedback.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    messages.success(request, _("Feedback approved. Publish it to share it with the employee."))
    return redirect(feedback.get_absolute_url())


@login_required
@permission_required("vertex.change_feedbackapproval")
@post_only
def feedback_publish(request, pk):
    feedback = _feedback_or_404(request, pk)
    if feedback.status != FeedbackStatus.APPROVED:
        messages.error(request, _("Approve the feedback before publishing."))
        return redirect(feedback.get_absolute_url())
    feedback.status = FeedbackStatus.PUBLISHED
    feedback.published_at = timezone.now()
    feedback.save(update_fields=["status", "published_at", "updated_at"])
    me = access.employee_of(request.user)
    notify_employees(
        me,
        [feedback.subject],
        str(_("Your approved feedback for %(period)s has been published.")) % {"period": feedback.period},
        reverse("vertex-my-performance"),
        icon="chatbubble-ellipses-outline",
    )
    average = feedback.get_average_score()
    pip = None
    if average is not None:
        pip = performance.evaluate_and_trigger_pip(feedback.subject, average, feedback.period, me)
    messages.success(request, _("Feedback published to the employee."))
    if pip is not None:
        messages.warning(
            request,
            _("The average score is below the threshold, so an improvement plan was started for %(who)s.")
            % {"who": feedback.subject.get_full_name()},
        )
    return redirect(feedback.get_absolute_url())


# ---------------------------------------------------------------------------
# Performance reports
# ---------------------------------------------------------------------------


def _report_or_404(request, pk):
    report = access.visible_reports(request.user).filter(pk=pk).first()
    if report is None:
        raise Http404
    return report


@login_required
@permission_required("vertex.view_performancereport")
def performance_report(request, pk):
    report = _report_or_404(request, pk)
    return render(
        request,
        "vertex/performance_report.html",
        {
            "report": report,
            "rows": performance.category_rows(report),
            "can_publish": access.can_publish_reports(request.user) and not report.is_published,
            "can_regenerate": request.user.has_perm("vertex.add_performancereport"),
            "bands": performance.BANDS,
        },
    )


@login_required
@permission_required("vertex.view_performancereport")
def performance_report_print(request, pk):
    """The same report as a printable page: open it, then "Save as PDF" in the browser."""
    report = _report_or_404(request, pk)
    return render(
        request,
        "vertex/performance_report_print.html",
        {"report": report, "rows": performance.category_rows(report)},
    )


@login_required
@permission_required("vertex.change_performancepublication")
@post_only
def performance_report_publish(request, pk):
    report = _report_or_404(request, pk)
    if report.is_published:
        messages.info(request, _("This report is already published."))
        return redirect(report.get_absolute_url())
    me = access.employee_of(request.user)
    report.is_published = True
    report.published_at = timezone.now()
    report.approved_by = me
    report.save(update_fields=["is_published", "published_at", "approved_by", "updated_at"])
    notify_employees(
        me,
        [report.subject],
        str(_("Your approved performance report for %(period)s has been published.")) % {"period": report.period},
        reverse("vertex-my-performance"),
        icon="analytics-outline",
    )
    pip = performance.evaluate_and_trigger_pip(report.subject, report.overall_score, report.period, me)
    messages.success(request, _("Report published to the employee."))
    if pip is not None:
        messages.warning(
            request,
            _("The score is below the threshold, so an improvement plan was started for %(who)s.")
            % {"who": report.subject.get_full_name()},
        )
    return redirect(report.get_absolute_url())


# ---------------------------------------------------------------------------
# My Performance (the employee's own approved view)
# ---------------------------------------------------------------------------


@login_required
@permission_required("vertex.view_myperformance")
def my_performance(request):
    reports = list(access.my_published_reports(request.user))
    latest = reports[-1] if reports else None
    trend = [{"period": r.period, "score": float(r.overall_score), "rating": r.get_rating_display()} for r in reports]
    feedback = list(access.my_published_feedback(request.user))
    return render(
        request,
        "vertex/my_performance.html",
        {
            "latest": latest,
            "rows": performance.category_rows(latest) if latest else [],
            "trend": trend,
            "feedback": feedback,
            "pips": access.my_pips(request.user),
            "appraisals": access.my_released_appraisals(request.user),
            "promotions": access.my_final_promotions(request.user),
            "training": access.my_training(request.user),
            "employee": access.employee_of(request.user),
        },
    )
