"""
vertex/expenses.py

The expense workflow of ArielVertex (Expenses controller, expense settings and the daily / weekly
summary jobs), with its gaps fixed:

    Payment requested -> (approver) approved or rejected -> (whoever raised it) paid

* when the company rule says every expense needs approval, nobody can skip it; ArielVertex let the
  person raising the expense decide (its settings default was never applied);
* an approver cannot approve or reject an expense they raised themselves, and a rejection needs a reason;
* a step locks the row and re-checks the status, so two clicks cannot do it twice;
* invoices are a PDF or a picture up to 10 MB, stored under a random name and opened only through an
  authorised download;
* the summaries use the local day (ArielVertex cut the day at UTC midnight) and are sent once even if
  several server processes are running.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _t

from employee.models import Employee
from vertex.models import Expense, ExpenseSetting, ExpenseStatus, VertexSetting
from vertex.notify import employees_with_perm, notify_employees, send_plain_email

logger = logging.getLogger(__name__)


class ExpenseError(Exception):
    """A rule of the workflow stopped the action; the message is meant for the person who clicked."""


def _locked(expense_id, expected):
    expense = Expense.objects.select_for_update(of=("self",)).select_related("raised_by").get(pk=expense_id)
    if expense.status not in expected:
        raise ExpenseError(
            _t("This expense is now '%(status)s', so that step is no longer possible.")
            % {"status": expense.get_status_display()}
        )
    return expense


def attach_invoice(expense, upload):
    """Store the invoice (replacing an earlier one); ``upload`` was already checked by the form."""
    old = expense.invoice.name if expense.invoice else None
    expense.invoice.save(upload.name, upload, save=False)
    expense.invoice_name = upload.name[:255]
    expense.invoice_size = upload.size
    expense.save(update_fields=["invoice", "invoice_name", "invoice_size"])
    if old and old != expense.invoice.name:
        expense.invoice.storage.delete(old)


@transaction.atomic
def raise_expense(expense, actor, upload=None):
    """Save a new request. The company rule decides whether it needs approval; approvers are told."""
    rule = ExpenseSetting.current().approval_required_by_default
    expense.raised_by = actor
    expense.approval_required = True if rule else bool(expense.approval_required)
    expense.status = ExpenseStatus.PAYMENT_REQUESTED
    expense.save()
    if upload is not None:
        attach_invoice(expense, upload)
    if expense.approval_required:
        notify_employees(
            actor, list(employees_with_perm("vertex.change_expenseapproval")),
            _t("'%(title)s' (%(amount)s) is waiting for your approval.")
            % {"title": expense.title, "amount": expense.get_amount_display()},
            expense.get_absolute_url(), icon="wallet-outline",
        )
    return expense


@transaction.atomic
def approve(expense_id, actor, note=""):
    expense = _locked(expense_id, [ExpenseStatus.PAYMENT_REQUESTED])
    if not expense.approval_required:
        raise ExpenseError(_t("This expense does not need approval."))
    if expense.raised_by_id == actor.pk:
        raise ExpenseError(_t("You cannot decide an expense you raised yourself."))
    expense.status = ExpenseStatus.APPROVED
    expense.approver = actor
    expense.decided_at = timezone.now()
    expense.decision_note = (note or "").strip()
    expense.save(update_fields=["status", "approver", "decided_at", "decision_note"])
    notify_employees(
        actor, [expense.raised_by],
        _t("'%(title)s' was approved. You can mark it paid.") % {"title": expense.title},
        expense.get_absolute_url(), icon="wallet-outline",
    )
    return expense


@transaction.atomic
def reject(expense_id, actor, note):
    if not (note or "").strip():
        raise ExpenseError(_t("Give a reason for rejecting the expense."))
    expense = _locked(expense_id, [ExpenseStatus.PAYMENT_REQUESTED])
    if not expense.approval_required:
        raise ExpenseError(_t("This expense does not need approval."))
    if expense.raised_by_id == actor.pk:
        raise ExpenseError(_t("You cannot decide an expense you raised yourself."))
    expense.status = ExpenseStatus.REJECTED
    expense.approver = actor
    expense.decided_at = timezone.now()
    expense.decision_note = note.strip()
    expense.save(update_fields=["status", "approver", "decided_at", "decision_note"])
    notify_employees(
        actor, [expense.raised_by],
        _t("'%(title)s' was rejected: %(note)s") % {"title": expense.title, "note": note.strip()},
        expense.get_absolute_url(), icon="wallet-outline",
    )
    return expense


@transaction.atomic
def pay(expense_id, actor):
    expense = _locked(expense_id, [ExpenseStatus.PAYMENT_REQUESTED, ExpenseStatus.APPROVED])
    if expense.approval_required and expense.status != ExpenseStatus.APPROVED:
        raise ExpenseError(_t("This expense needs approval before it can be marked paid."))
    expense.status = ExpenseStatus.PAID
    expense.paid_by = actor
    expense.paid_at = timezone.now()
    expense.save(update_fields=["status", "paid_by", "paid_at"])
    return expense


# ---------------------------------------------------------------------------
# Daily and weekly summaries
# ---------------------------------------------------------------------------


def _money(amount):
    return f"₹{amount:,.0f}"


def build_summary(kind, today=None):
    """(title, one-line message, full body) for 'daily' (today) or 'weekly' (the last 7 days)."""
    from django.db.models import Q, Sum

    today = today or timezone.localdate()
    start = today if kind == "daily" else today - timedelta(days=7)
    expenses = Expense.objects.filter(created_at__date__gte=start).select_related("raised_by")
    totals = expenses.aggregate(
        total=Sum("amount"), paid=Sum("amount", filter=Q(status=ExpenseStatus.PAID))
    )
    count = expenses.count()
    pending = expenses.filter(status=ExpenseStatus.PAYMENT_REQUESTED, approval_required=True).count()
    total, paid = totals["total"] or 0, totals["paid"] or 0
    label = _t("Daily") if kind == "daily" else _t("Weekly")
    title = _t("%(label)s expense summary - %(date)s") % {"label": label, "date": f"{today:%d %b %Y}"}
    message = _t("%(n)s expense(s), %(total)s in total; %(pending)s waiting for approval.") % {
        "n": count, "total": _money(total), "pending": pending,
    }
    lines = [
        f"- {e.title} - {_money(e.amount)} [{e.get_status_display()}]"
        for e in expenses.order_by("-created_at")[:15]
    ]
    body = (
        _t("%(n)s expense(s), total %(total)s. Paid %(paid)s, %(pending)s waiting for approval.")
        % {"n": count, "total": _money(total), "paid": _money(paid), "pending": pending}
        + "\n\n"
        + "\n".join(lines)
    )
    return title, message, body


def _post_teams(url, title, body):
    import requests

    response = requests.post(url, json={"title": title, "text": body.replace("\n", "\n\n")}, timeout=10)
    response.raise_for_status()


def send_summary(kind):
    """Send a summary now: bell notification, e-mail and (when set) the Teams channel; best effort."""
    setting = ExpenseSetting.current()
    raw = setting.daily_recipients if kind == "daily" else setting.weekly_recipients
    emails = [e.strip().lower() for e in raw.split(",") if e.strip()]
    title, message, body = build_summary(kind)
    result = {"recipients": len(emails), "emailed": 0, "teams": False}
    if emails:
        internal = Employee.objects.filter(email__in=emails, is_active=True)
        sender = Employee.objects.filter(employee_user_id__is_superuser=True, is_active=True).first()
        if sender is not None:  # a bell notification needs someone to come from
            notify_employees(sender, list(internal), f"{title}: {message}", reverse("vertex-expenses"), icon="wallet-outline")
        result["emailed"] = send_plain_email(emails, title, body)
    if setting.teams_webhook_url:
        try:
            _post_teams(setting.teams_webhook_url, title, body)
            result["teams"] = True
        except Exception:
            logger.exception("Vertex expense summary could not be posted to Teams")
    return result


def _claim(key, today):
    """True for exactly one caller per day, even with several server processes running."""
    VertexSetting.objects.get_or_create(key=key, defaults={"value": ""})
    return VertexSetting.objects.filter(key=key).exclude(value=today.isoformat()).update(value=today.isoformat()) == 1


def run_due_summaries(now=None):
    """The scheduler tick: the daily summary after 18:00, the weekly one on Fridays after 18:00."""
    now = timezone.localtime(now or timezone.now())
    sent = []
    if now.hour >= 18 and _claim("expenses.last_daily", now.date()):
        send_summary("daily")
        sent.append("daily")
    if now.weekday() == 4 and now.hour >= 18 and _claim("expenses.last_weekly", now.date()):
        send_summary("weekly")
        sent.append("weekly")
    return sent
