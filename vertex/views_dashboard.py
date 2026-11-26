"""
vertex/views_dashboard.py

The Vertex overview (a role-aware home), the talent analytics page and the payroll CSV of
completed promotions. The numbers are built in vertex/dashboard.py and vertex/analytics.py.
"""

from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from horilla.decorators import login_required, permission_required
from vertex import access, analytics, audit, dashboard
from vertex.notify_rules import RULES


@login_required
def home(request):
    """The Vertex entry point: the overview for people who have it, otherwise the projects list."""
    if request.user.has_perm("vertex.view_vertexdashboard"):
        return redirect("vertex-overview")
    return redirect("vertex-projects")


@login_required
@permission_required("vertex.view_vertexdashboard")
def overview(request):
    context = dashboard.dashboard_for(request.user)
    context["can_analytics"] = request.user.has_perm("vertex.view_talentanalytics")
    context["rules"] = RULES
    return render(request, "vertex/dashboard.html", context)


@login_required
@permission_required("vertex.view_talentanalytics")
def talent_analytics(request):
    report = analytics.talent_report()
    context = {
        "report": report,
        "can_export": request.user.has_perm("vertex.view_payrollexport"),
        "chart_data": {
            "ratings": report["ratings"],
            "departments": report["departments"],
            "promotions": report["promotions"],
            "training_status": report["training"]["by_status"],
            "training_gap": report["training"]["by_gap"],
        },
    }
    return render(request, "vertex/analytics.html", context)


@login_required
@permission_required("vertex.view_payrollexport")
def payroll_export(request):
    filename = f"payroll-revisions-{timezone.localdate().isoformat()}.csv"
    content = analytics.payroll_csv()
    me = access.employee_of(request.user)
    if me is not None:  # the salary figures are the most sensitive thing Vertex hands out: record who took them
        audit.log_access(me, f"downloaded the payroll CSV ({max(content.count(chr(10)) - 1, 0)} rows)")
    response = HttpResponse(content, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
