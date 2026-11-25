"""
vertex/views_reports.py

Management reports: the Reports page (team size per project, resource availability, the project
register), its CSV, and the printable management report (open it, then "Save as PDF"). Every
number is built from the projects the person can open.
"""

import csv
import io

from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from horilla.decorators import login_required, permission_required
from vertex import access
from vertex.analytics import _safe_cell
from vertex.models import Project, ProjectHealth, ProjectStatus

CSV_HEADER = ["Code", "Name", "Status", "Health", "Priority", "Client", "Members"]


def report_data(user):
    projects = list(access.visible_projects(user).order_by("code"))
    buckets = None
    if user.has_perm("vertex.view_resourcevisibility"):
        _rows, buckets = access.occupancy()
    members = {p.pk: p.get_member_count() for p in projects}
    return {
        "projects": [{"project": p, "members": members[p.pk]} for p in projects],
        "kpis": {
            "projects": len(projects),
            "active": sum(1 for p in projects if p.status == ProjectStatus.ACTIVE),
            "at_risk": sum(1 for p in projects if p.health != ProjectHealth.GREEN),
            "assignments": sum(members.values()),
        },
        "buckets": buckets,
        "chart": {"labels": [p.code for p in projects], "values": [members[p.pk] for p in projects]},
    }


@login_required
@permission_required("vertex.view_managementreport")
def reports(request):
    return render(request, "vertex/reports.html", report_data(request.user))


@login_required
@permission_required("vertex.view_managementreport")
def reports_print(request):
    context = report_data(request.user)
    context["today"] = timezone.localdate()
    return render(request, "vertex/reports_print.html", context)


@login_required
@permission_required("vertex.view_managementreport")
def reports_csv(request):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_HEADER)
    for row in report_data(request.user)["projects"]:
        p = row["project"]
        writer.writerow(
            [
                _safe_cell(p.code), _safe_cell(p.name), p.get_status_display(), p.get_health_display(),
                p.get_priority_display(), _safe_cell(p.client_name), row["members"],
            ]
        )
    response = HttpResponse(buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="vertex-projects-{timezone.localdate().isoformat()}.csv"'
    response["X-Content-Type-Options"] = "nosniff"
    return response
