"""
vertex/views_expenses.py

The expense detail page, the authorised invoice download, "mark paid", the settings page and the
"send the summary now" action. Lists and modal forms are in vertex/cbv_expenses.py.
"""

from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from horilla.decorators import login_required, permission_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access, audit, expenses
from vertex.models import ExpenseSetting, ExpenseStatus


def _expense_or_404(request, pk):
    expense = access.visible_expenses(request.user).filter(pk=pk).first()
    if expense is None:
        raise Http404
    return expense


@login_required
@permission_required("vertex.view_expense")
def expense_detail(request, pk):
    expense = _expense_or_404(request, pk)
    return render(
        request,
        "vertex/expense_detail.html",
        {
            "expense": expense,
            "can_approve": access.can_approve_expense(request.user, expense),
            "can_pay": access.can_pay_expense(request.user, expense),
            "can_upload": access.can_upload_invoice(request.user, expense),
            "can_download": access.can_download_invoice(request.user, expense),
            "rejected": expense.status == ExpenseStatus.REJECTED,
        },
    )


@login_required
@permission_required("vertex.view_expense")
def expense_download(request, pk):
    expense = _expense_or_404(request, pk)
    if not access.can_download_invoice(request.user, expense):
        raise Http404
    response = FileResponse(expense.invoice.open("rb"), as_attachment=True, filename=expense.invoice_name or "invoice")
    response["X-Content-Type-Options"] = "nosniff"
    audit.log_access(expense, "downloaded the invoice")
    return response


@login_required
@permission_required("vertex.change_expense")
@post_only
def expense_pay(request, pk):
    expense = _expense_or_404(request, pk)
    if not access.can_pay_expense(request.user, expense):
        return handle_no_permission(request)
    try:
        expenses.pay(expense.pk, access.employee_of(request.user))
    except expenses.ExpenseError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, _("Expense marked as paid."))
    return redirect("vertex-expenses")


@login_required
@permission_required("vertex.view_expensesetting")
def expense_settings(request):
    return render(
        request,
        "vertex/expense_settings.html",
        {
            "setting": ExpenseSetting.current(),
            "can_change": request.user.has_perm("vertex.change_expensesetting"),
        },
    )


@login_required
@permission_required("vertex.change_expensesetting")
@post_only
def expense_summary_now(request, kind):
    if kind not in ("daily", "weekly"):
        raise Http404
    result = expenses.send_summary(kind)
    if result["recipients"] or result["teams"]:
        messages.success(
            request,
            _("The %(kind)s summary was sent: %(n)s recipient(s), %(m)s e-mail(s) delivered%(teams)s.")
            % {
                "kind": kind, "n": result["recipients"], "m": result["emailed"],
                "teams": _(", posted to Teams") if result["teams"] else "",
            },
        )
    else:
        messages.warning(request, _("Nobody is set to receive the %(kind)s summary yet. Add recipients first.") % {"kind": kind})
    return redirect("vertex-expense-settings")
