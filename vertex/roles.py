"""
vertex/roles.py

Example Vertex roles for Ariel HR: each one is an ordinary Django Group holding standard
permissions, shown and edited in the Groups and Permissions grid. The role matrix tests and
the ``vertex_demo_users`` command both build users from this one definition, so the rules that
are tested are exactly the ones you can set up by hand.

Admin (superuser) needs none of this and passes every check.
"""

# permission codenames of the vertex app, grouped by what they allow
PROJECTS_VIEW = ["view_project", "view_vertexdashboard"]
PROJECTS_MANAGE = ["add_project", "change_project", "add_projectmember", "delete_projectmember",
                   "add_projectdocument", "delete_projectdocument", "add_projectcall"]
PROJECTS_ALL = ["view_allprojects", "change_allprojects"]

ROLES = {
    "Vertex - HR Manager": [
        *PROJECTS_VIEW, *PROJECTS_MANAGE, *PROJECTS_ALL,
        "view_statusupdate", "view_allstatusupdates",
        "view_resourcerequest", "add_resourcerequest", "change_resourcerequest",
        "view_allresourcerequests", "view_resourcevisibility",
        "view_reviewrequest", "add_reviewrequest", "change_reviewrequest", "view_allreviewrequests",
        "add_projectreview", "add_codereview",
        "view_meetingminute", "add_meetingminute", "change_meetingminute", "delete_meetingminute",
        "view_feedback", "add_feedback", "change_feedback", "view_allfeedback", "change_feedbackapproval",
        "view_performancereport", "add_performancereport", "view_allperformancereports",
        "change_performancepublication",
        "view_pip", "add_pip", "change_pip",
        "view_myperformance",
        "view_appraisalcycle", "add_appraisalcycle", "change_appraisalcycle",
        "view_appraisal", "change_appraisal", "view_allappraisals", "change_appraisalrelease",
        "view_goal", "add_goal", "change_goal", "delete_goal", "view_allgoals",
        "view_promotion", "add_promotion", "view_allpromotions", "change_promotionvalidation",
        "view_trainingrecommendation", "add_trainingrecommendation", "change_trainingrecommendation",
        "delete_trainingrecommendation", "view_alltraining",
        "view_expense", "view_allexpenses", "view_expensesetting",
        "view_talentanalytics", "view_payrollexport", "view_managementreport",
    ],
    "Vertex - Project Manager": [
        *PROJECTS_VIEW, "change_project", "add_projectmember", "delete_projectmember",
        "add_projectdocument", "delete_projectdocument", "add_projectcall",
        "view_statusupdate", "add_statusupdate",
        "view_resourcerequest", "add_resourcerequest", "view_resourcevisibility",
        "view_reviewrequest", "change_reviewrequest", "add_projectreview",
        "view_meetingminute", "add_meetingminute", "change_meetingminute", "delete_meetingminute",
        "view_feedback", "add_feedback", "change_feedback",
        "view_myperformance",
        "view_appraisal", "change_appraisal",
        "view_goal", "add_goal", "change_goal", "delete_goal",
        "view_promotion", "add_promotion",
        "view_trainingrecommendation", "add_trainingrecommendation", "change_trainingrecommendation",
        "delete_trainingrecommendation",
        "view_managementreport",
    ],
    "Vertex - Technical Lead": [
        *PROJECTS_VIEW, "add_projectdocument",
        "view_statusupdate", "add_statusupdate",
        "view_reviewrequest", "add_codereview", "add_projectreview",
        "view_feedback", "add_feedback", "change_feedback",
        "view_myperformance",
        "view_appraisal", "view_goal", "view_trainingrecommendation",
    ],
    "Vertex - Developer": [
        *PROJECTS_VIEW, "add_projectdocument",
        "view_statusupdate", "add_statusupdate",
        "view_reviewrequest",
        "view_myperformance",
        "view_appraisal", "view_goal", "view_trainingrecommendation",
    ],
    "Vertex - Business Person": [
        *PROJECTS_VIEW, "view_statusupdate",
    ],
    "Vertex - Leadership (read only)": [
        *PROJECTS_VIEW, "view_allprojects", "view_statusupdate", "view_resourcevisibility",
        "view_allreviewrequests", "view_reviewrequest",
        "view_appraisalcycle", "view_appraisal", "view_allappraisals", "view_goal", "view_allgoals",
        "view_promotion", "view_allpromotions", "view_trainingrecommendation", "view_alltraining",
        "view_expense", "view_allexpenses",
        "view_talentanalytics", "view_managementreport",
    ],
    "Vertex - Leadership Approver": [
        *PROJECTS_VIEW, "view_allprojects", "view_statusupdate", "view_resourcevisibility",
        "view_appraisal", "view_allappraisals", "view_goal", "view_allgoals",
        "view_promotion", "view_allpromotions", "change_promotionapproval",
        "view_trainingrecommendation", "view_alltraining",
        "view_expense", "view_allexpenses",
        "view_talentanalytics", "view_payrollexport", "view_managementreport",
    ],
    # finance: approves and sees every expense, and can read the expense settings
    "Vertex - Accountant": [
        *PROJECTS_VIEW, "view_expense", "view_allexpenses", "change_expenseapproval", "view_expensesetting",
    ],
    # front desk: raises payment requests, uploads the invoice and marks its own expenses paid
    "Vertex - Front Desk": [*PROJECTS_VIEW, "view_expense", "add_expense", "change_expense"],
}

# (username, first name, last name, group or None, role on the demo project or None)
DEMO_USERS = [
    ("vx.hr", "Hema", "Rao", "Vertex - HR Manager", None),
    ("vx.pm", "Priya", "Mehta", "Vertex - Project Manager", "project_manager"),
    ("vx.lead", "Tarun", "Lal", "Vertex - Technical Lead", "technical_lead"),
    ("vx.dev", "Dev", "Kumar", "Vertex - Developer", "developer"),
    ("vx.dev2", "Divya", "Nair", "Vertex - Developer", "developer"),
    ("vx.biz", "Bhavna", "Shah", "Vertex - Business Person", "business_person"),
    ("vx.boss", "Lakshmi", "Iyer", "Vertex - Leadership (read only)", None),
    ("vx.ceo", "Chitra", "Rao", "Vertex - Leadership Approver", None),
    ("vx.acct", "Anita", "Desai", "Vertex - Accountant", None),
    ("vx.desk", "Farhan", "Khan", "Vertex - Front Desk", None),
    ("vx.none", "Nina", "Nobody", None, "member"),
]
# (employee, their reporting manager): appraisals and goals follow this
REPORTING = [("vx.dev", "vx.pm"), ("vx.dev2", "vx.pm"), ("vx.pm", "vx.hr"), ("vx.lead", "vx.pm"), ("vx.biz", "vx.hr")]
DEMO_PROJECT_CODE = "VXDEMO"
DEMO_PASSWORD = "Vx-Demo-4827!"  # local test accounts only; removed with `vertex_demo_users --remove`


def build_groups():
    """Create or update every role group; returns {name: Group}."""
    from django.contrib.auth.models import Group, Permission

    groups = {}
    for name, codenames in ROLES.items():
        group, _created = Group.objects.get_or_create(name=name)
        permissions = Permission.objects.filter(content_type__app_label="vertex", codename__in=codenames)
        missing = set(codenames) - set(permissions.values_list("codename", flat=True))
        if missing:
            raise ValueError(f"{name}: unknown permissions {sorted(missing)}")
        group.permissions.set(permissions)
        groups[name] = group
    return groups
