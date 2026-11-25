"""
vertex/access.py

Who may see or manage which Vertex project. Permissions are ordinary Ariel HR
(Django) permissions, so groups, users and the admin work as everywhere else. A
superuser (Admin) always passes.

Rules, ported from ArielVertex ProjectAccessService:

* see the Projects pages      -> vertex.view_project
* see EVERY project           -> vertex.view_allprojects (else only projects the user is on)
* manage a project            -> vertex.change_allprojects, or vertex.change_project AND
                                 being project manager / coordinator on that project
* business people on a project never see Internal documents
"""

from django.db.models import Q

from vertex.models import Project, ProjectMember, ProjectRole, Visibility

MANAGER_ROLES = (ProjectRole.PROJECT_MANAGER, ProjectRole.PROJECT_COORDINATOR)


def employee_of(user):
    return getattr(user, "employee_get", None)


def has_vertex_access(user):
    """Anyone who holds at least one Vertex permission (admin included) may use the Vertex notification centre."""
    return bool(
        user.is_superuser or any(perm.startswith("vertex.") for perm in user.get_all_permissions())
    )


def sees_all_projects(user):
    return user.has_perm("vertex.view_allprojects")


def membership(user, project):
    employee = employee_of(user)
    if employee is None:
        return None
    return ProjectMember.objects.filter(
        project=project, employee=employee, is_active=True
    ).first()


def visible_projects(user):
    """Projects the user may open."""
    if not user.has_perm("vertex.view_project"):
        return Project.objects.none()
    if sees_all_projects(user):
        return Project.objects.all()
    employee = employee_of(user)
    if employee is None:
        return Project.objects.none()
    return Project.objects.filter(
        Q(members__employee=employee, members__is_active=True)
    ).distinct()


def can_view(user, project):
    return visible_projects(user).filter(pk=project.pk).exists()


def my_role(user, project):
    member = membership(user, project)
    return member.role_on_project if member else None


def can_manage(user, project):
    if user.has_perm("vertex.change_allprojects"):
        return True
    if not user.has_perm("vertex.change_project"):
        return False
    return my_role(user, project) in MANAGER_ROLES


def is_business_only(user, project):
    """A business person on the project (without see-all) is kept away from internal items."""
    return (
        my_role(user, project) == ProjectRole.BUSINESS_PERSON
        and not sees_all_projects(user)
    )


def visible_documents(user, project):
    documents = project.documents.select_related("uploaded_by")
    if is_business_only(user, project):
        documents = documents.exclude(visibility=Visibility.INTERNAL)
    return documents


def can_open_document(user, document):
    """Rule for /media/: the file of a document opens only for people who can see it."""
    if not can_view(user, document.project):
        return False
    return not (
        is_business_only(user, document.project)
        and document.visibility == Visibility.INTERNAL
    )


# ---------------------------------------------------------------------------
# Reviews (ArielVertex ReviewsController)
# ---------------------------------------------------------------------------


def sees_all_reviews(user):
    """Requesters and holders of the see-all switch see every review request."""
    return user.has_perm("vertex.view_allreviewrequests") or user.has_perm(
        "vertex.add_reviewrequest"
    )


def visible_reviews(user):
    from vertex.models import ReviewRequest

    if not user.has_perm("vertex.view_reviewrequest"):
        return ReviewRequest.objects.none()
    reviews = ReviewRequest.objects.select_related(
        "project", "subject", "requested_by", "assigned_to"
    )
    if sees_all_reviews(user):
        return reviews
    employee = employee_of(user)
    if employee is None:
        return reviews.none()
    return reviews.filter(
        Q(assigned_to=employee) | Q(subject=employee) | Q(requested_by=employee)
    )


def can_schedule_review(user, review):
    return user.has_perm("vertex.change_reviewrequest") and can_manage(user, review.project)


def can_submit_review(user, review):
    """Only the assigned reviewer, or someone who sees every review, may submit the outcome."""
    perm = (
        "vertex.add_codereview"
        if review.review_type == "code_review"
        else "vertex.add_projectreview"
    )
    if not user.has_perm(perm):
        return False
    employee = employee_of(user)
    return (
        employee is not None and review.assigned_to_id == employee.pk
    ) or user.has_perm("vertex.view_allreviewrequests")


# ---------------------------------------------------------------------------
# Status updates (ArielVertex StatusUpdatesController)
# ---------------------------------------------------------------------------


def assignable_projects(user):
    """Projects the person may submit a status update for: those they are an active member of."""
    employee = employee_of(user)
    if employee is None:
        return Project.objects.none()
    return Project.objects.filter(
        members__employee=employee, members__is_active=True
    ).distinct()


def can_see_internal_status(user, project):
    """Internal notes and the consolidated summary: see-all holders and the project managers."""
    return user.has_perm("vertex.view_allstatusupdates") or can_manage(user, project)


def visible_status_updates(user, project):
    """
    Updates of a project as the user may see them. Business people get only a business-safe
    projection (see ``business_view``); everyone else sees the full update.
    """
    if not user.has_perm("vertex.view_statusupdate"):
        return project.status_updates.none()
    return project.status_updates.select_related("employee", "project")


def business_view(user, project):
    """True when status updates must be shown stripped (no internal note, blockers or detail)."""
    return is_business_only(user, project) and not can_see_internal_status(user, project)


def consolidated_summary(project, days=7):
    """
    Business-ready summary of the last ``days`` days: the client-shareable updates, and who
    of the developers / QA on the project has not reported (ArielVertex Consolidated).
    """
    from datetime import timedelta

    from django.utils import timezone

    days = max(1, min(int(days), 60))
    start = timezone.localdate() - timedelta(days=days)
    updates = list(
        project.status_updates.filter(update_date__gte=start).select_related("employee")
    )
    expected = list(
        project.members.filter(
            is_active=True, role_on_project__in=[ProjectRole.DEVELOPER, ProjectRole.QA]
        ).select_related("employee")
    )
    reporting_ids = {u.employee_id for u in updates}
    return {
        "days": days,
        "start": start,
        "updates": updates,
        # only people the project expects an update from count, so "reporting" never exceeds "expected"
        "reporting": sum(1 for m in expected if m.employee_id in reporting_ids),
        "expected": len(expected),
        "missing": [m.employee.get_full_name() for m in expected if m.employee_id not in reporting_ids],
    }


# ---------------------------------------------------------------------------
# Resources (ArielVertex ResourcesController + ResourceRequestsController)
# ---------------------------------------------------------------------------

AVAILABILITY_ORDER = ("Free", "Partially Available", "Fully Allocated", "Overloaded")


def availability_for(total):
    """Allocation bucket, ported from ArielVertex: 0 free, under 60 partial, up to 100 full, above overloaded."""
    if total <= 0:
        return "Free"
    if total < 60:
        return "Partially Available"
    if total <= 100:
        return "Fully Allocated"
    return "Overloaded"


def occupancy(search=""):
    """
    Allocation of every active employee across projects.

    ArielVertex only lists people who already have a membership, so its Free bucket is always
    empty. Here every active employee is listed and a person with no allocation is Free.
    Allocations of members who have left the project (end date passed) are not counted.
    """
    from django.utils import timezone

    from employee.models import Employee

    today = timezone.localdate()
    memberships = ProjectMember.objects.filter(is_active=True).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today)
    ).select_related("project")
    lines = {}
    for member in memberships:
        lines.setdefault(member.employee_id, []).append(member)

    employees = Employee.objects.filter(is_active=True).order_by(
        "employee_first_name", "employee_last_name"
    )
    if search:
        employees = employees.filter(
            Q(employee_first_name__icontains=search)
            | Q(employee_last_name__icontains=search)
            | Q(badge_id__icontains=search)
        )
    rows = []
    for employee in employees:
        own = lines.get(employee.pk, [])
        total = sum(m.allocation_pct for m in own)
        rows.append(
            {
                "employee": employee,
                "projects": own,
                "total": total,
                "availability": availability_for(total),
            }
        )
    rows.sort(key=lambda r: -r["total"])
    buckets = [
        {"name": name, "count": sum(1 for r in rows if r["availability"] == name)}
        for name in AVAILABILITY_ORDER
    ]
    return rows, buckets


def sees_all_resource_requests(user):
    return user.has_perm("vertex.view_allresourcerequests") or user.has_perm(
        "vertex.change_resourcerequest"
    )


def visible_resource_requests(user):
    from vertex.models import ResourceRequest

    if not user.has_perm("vertex.view_resourcerequest"):
        return ResourceRequest.objects.none()
    requests = ResourceRequest.objects.select_related("project", "requested_by")
    if sees_all_resource_requests(user):
        return requests
    employee = employee_of(user)
    if employee is None:
        return requests.none()
    return requests.filter(requested_by=employee)


# ---------------------------------------------------------------------------
# Performance: feedback, reports, improvement plans
# ---------------------------------------------------------------------------


def can_approve_feedback(user):
    return user.has_perm("vertex.change_feedbackapproval")


def sees_all_feedback(user):
    return user.has_perm("vertex.view_allfeedback") or can_approve_feedback(user)


def visible_feedback(user):
    """Feedback a manager may open: everyone's for HR / approvers, otherwise what they wrote."""
    from vertex.models import Feedback

    if not user.has_perm("vertex.view_feedback"):
        return Feedback.objects.none()
    feedback = Feedback.objects.select_related("subject", "author", "project").prefetch_related(
        "category_scores"
    )
    if sees_all_feedback(user):
        return feedback
    employee = employee_of(user)
    return feedback.filter(author=employee) if employee else feedback.none()


def can_edit_feedback(user, feedback):
    """The author may edit their own feedback while it is a draft or sent back for revision."""
    from vertex.models import FeedbackStatus

    employee = employee_of(user)
    return bool(
        user.has_perm("vertex.change_feedback")
        and employee is not None
        and feedback.author_id == employee.pk
        and feedback.status in (FeedbackStatus.DRAFT, FeedbackStatus.REVISION_REQUESTED)
    )


def visible_reports(user):
    """Performance reports: all of them (drafts too) with the all-reports switch, else only your own published ones."""
    from vertex.models import PerformanceReport

    if not user.has_perm("vertex.view_performancereport"):
        return PerformanceReport.objects.none()
    reports = PerformanceReport.objects.select_related("subject").prefetch_related("category_scores")
    if user.has_perm("vertex.view_allperformancereports"):
        return reports
    employee = employee_of(user)
    return reports.filter(subject=employee, is_published=True) if employee else reports.none()


def can_publish_reports(user):
    return user.has_perm("vertex.change_performancepublication")


def my_published_reports(user):
    from vertex.models import PerformanceReport

    employee = employee_of(user)
    if employee is None:
        return PerformanceReport.objects.none()
    return (
        PerformanceReport.objects.filter(subject=employee, is_published=True)
        .prefetch_related("category_scores")
        .order_by("period")
    )


def my_published_feedback(user):
    """What the employee may read: published feedback only, never the internal notes."""
    from vertex.models import Feedback, FeedbackStatus

    employee = employee_of(user)
    if employee is None:
        return Feedback.objects.none()
    return (
        Feedback.objects.filter(subject=employee, status=FeedbackStatus.PUBLISHED)
        .select_related("project", "author")
        .prefetch_related("category_scores")
        .order_by("-published_at")
    )


def my_pips(user):
    from vertex.models import Pip

    employee = employee_of(user)
    return Pip.objects.filter(subject=employee) if employee else Pip.objects.none()


def visible_pips(user):
    from vertex.models import Pip

    if not user.has_perm("vertex.view_pip"):
        return Pip.objects.none()
    return Pip.objects.select_related("subject", "created_by")


# ---------------------------------------------------------------------------
# Appraisals and goals
# ---------------------------------------------------------------------------


def reports_of(user):
    """Active employees whose reporting manager is this user."""
    from employee.models import Employee

    me = employee_of(user)
    if me is None:
        return Employee.objects.none()
    return Employee.objects.filter(employee_work_info__reporting_manager_id=me, is_active=True)


def is_manager_of(user, employee):
    me = employee_of(user)
    return bool(me is not None and employee.get_reporting_manager() == me)


def can_release_appraisals(user):
    return user.has_perm("vertex.change_appraisalrelease")


def sees_all_appraisals(user):
    return user.has_perm("vertex.view_allappraisals") or can_release_appraisals(user)


def visible_appraisals(user):
    """Everyone's for HR / leadership, your own plus your reports' when you evaluate, else only your own."""
    from django.db.models import Q

    from vertex.models import Appraisal

    if not user.has_perm("vertex.view_appraisal"):
        return Appraisal.objects.none()
    appraisals = Appraisal.objects.select_related("cycle", "employee", "manager")
    if sees_all_appraisals(user):
        return appraisals
    me = employee_of(user)
    if me is None:
        return appraisals.none()
    if user.has_perm("vertex.change_appraisal"):
        return appraisals.filter(Q(employee=me) | Q(manager=me))
    return appraisals.filter(employee=me)


def can_self_assess(user, appraisal):
    from vertex.models import AppraisalStage, CycleStatus

    me = employee_of(user)
    return bool(
        user.has_perm("vertex.view_appraisal")
        and me is not None
        and appraisal.employee_id == me.pk
        and appraisal.stage == AppraisalStage.SELF_PENDING
        and appraisal.cycle.status == CycleStatus.ACTIVE
    )


def can_evaluate(user, appraisal):
    """The assigned manager (or HR / leadership) once the self assessment is in; never for your own appraisal."""
    from vertex.models import AppraisalStage, CycleStatus

    me = employee_of(user)
    if me is None or not user.has_perm("vertex.change_appraisal"):
        return False
    if appraisal.employee_id == me.pk or appraisal.cycle.status != CycleStatus.ACTIVE:
        return False
    if appraisal.stage not in (AppraisalStage.SELF_SUBMITTED, AppraisalStage.MANAGER_COMPLETED):
        return False
    return appraisal.manager_id == me.pk or sees_all_appraisals(user)


def can_release(user, appraisal):
    from vertex.models import AppraisalStage, CycleStatus

    me = employee_of(user)
    return bool(
        can_release_appraisals(user)
        and me is not None
        and appraisal.employee_id != me.pk
        and appraisal.stage == AppraisalStage.MANAGER_COMPLETED
        and appraisal.cycle.status == CycleStatus.ACTIVE
    )


def can_see_manager_part(user, appraisal):
    """The employee reads the manager's rating and comments only once the appraisal is released."""
    from vertex.models import AppraisalStage

    me = employee_of(user)
    if me is not None and appraisal.employee_id == me.pk and not sees_all_appraisals(user):
        return appraisal.stage == AppraisalStage.RELEASED
    return True


def my_released_appraisals(user):
    from vertex.models import Appraisal, AppraisalStage

    me = employee_of(user)
    if me is None:
        return Appraisal.objects.none()
    return Appraisal.objects.filter(employee=me, stage=AppraisalStage.RELEASED).select_related("cycle")


def sees_all_goals(user):
    return user.has_perm("vertex.view_allgoals")


def visible_goals(user):
    from django.db.models import Q

    from vertex.models import Goal

    if not user.has_perm("vertex.view_goal"):
        return Goal.objects.none()
    goals = Goal.objects.select_related("employee", "assigned_by", "cycle")
    if sees_all_goals(user):
        return goals
    me = employee_of(user)
    if me is None:
        return goals.none()
    return goals.filter(Q(employee=me) | Q(employee__employee_work_info__reporting_manager_id=me))


def assignable_employees(user):
    """Who this person may set goals for: anyone for HR / leadership, otherwise the people who report to them."""
    from employee.models import Employee

    if not user.has_perm("vertex.add_goal"):
        return Employee.objects.none()
    if sees_all_goals(user):
        return Employee.objects.filter(is_active=True)
    return reports_of(user)


def can_edit_goal(user, goal):
    return user.has_perm("vertex.change_goal") and (
        sees_all_goals(user) or is_manager_of(user, goal.employee)
    )


def can_update_goal_progress(user, goal):
    me = employee_of(user)
    owner = me is not None and goal.employee_id == me.pk
    return user.has_perm("vertex.view_goal") and (owner or can_edit_goal(user, goal))


def can_delete_goal(user, goal):
    return user.has_perm("vertex.delete_goal") and (
        sees_all_goals(user) or is_manager_of(user, goal.employee)
    )


def weight_used(employee, cycle, exclude_pk=None):
    """Weight already given to the person's goals (not cancelled) in a cycle (or the no-cycle group)."""
    from django.db.models import Sum

    from vertex.models import Goal, GoalStatus

    goals = Goal.objects.filter(employee=employee, cycle=cycle).exclude(status=GoalStatus.CANCELLED)
    if exclude_pk:
        goals = goals.exclude(pk=exclude_pk)
    return goals.aggregate(total=Sum("weightage"))["total"] or 0


# ---------------------------------------------------------------------------
# Promotions and training
# ---------------------------------------------------------------------------


def can_validate_promotions(user):
    return user.has_perm("vertex.change_promotionvalidation")


def can_approve_promotions(user):
    return user.has_perm("vertex.change_promotionapproval")


def sees_all_promotions(user):
    return user.has_perm("vertex.view_allpromotions") or can_validate_promotions(user) or can_approve_promotions(user)


def visible_promotions(user):
    """Everyone's for HR / leadership; otherwise the ones you recommended or for people who report to you,
    plus your own once it is completed (the employee never sees a request that is still being decided
    or that did not go ahead)."""
    from django.db.models import Q

    from vertex.models import Promotion, PromotionStage

    if not user.has_perm("vertex.view_promotion"):
        return Promotion.objects.none()
    promotions = Promotion.objects.select_related("employee", "recommended_by", "proposed_position")
    if sees_all_promotions(user):
        return promotions
    me = employee_of(user)
    if me is None:
        return promotions.none()
    return promotions.filter(
        Q(recommended_by=me)
        | Q(employee__employee_work_info__reporting_manager_id=me)
        | (Q(employee=me) & Q(stage=PromotionStage.COMPLETED))
    )


def can_see_salary(user, promotion):
    """Salary figures: HR, leadership and the person who recommended; everyone else sees designations only."""
    me = employee_of(user)
    return bool(
        sees_all_promotions(user) or (me is not None and promotion.recommended_by_id == me.pk)
    )


def recommendable_employees(user):
    """Who may be put forward: anyone for HR / leadership, otherwise the people who report to you."""
    from employee.models import Employee

    if not user.has_perm("vertex.add_promotion"):
        return Employee.objects.none()
    if sees_all_promotions(user):
        return Employee.objects.filter(is_active=True)
    return reports_of(user)


def _not_own(user, promotion):
    me = employee_of(user)
    return me is not None and promotion.employee_id != me.pk


def can_validate(user, promotion):
    from vertex.models import PromotionStage

    return bool(can_validate_promotions(user) and promotion.stage == PromotionStage.RECOMMENDED and _not_own(user, promotion))


def can_approve(user, promotion):
    from vertex.models import PromotionStage

    return bool(can_approve_promotions(user) and promotion.stage == PromotionStage.HR_VALIDATED and _not_own(user, promotion))


def can_complete(user, promotion):
    from vertex.models import PromotionStage

    return bool(can_validate_promotions(user) and promotion.stage == PromotionStage.APPROVED and _not_own(user, promotion))


def can_reject(user, promotion):
    return bool(
        promotion.is_open
        and (can_validate_promotions(user) or can_approve_promotions(user))
        and _not_own(user, promotion)
    )


def my_final_promotions(user):
    from vertex.models import Promotion, PromotionStage

    me = employee_of(user)
    if me is None:
        return Promotion.objects.none()
    return Promotion.objects.filter(employee=me, stage=PromotionStage.COMPLETED).select_related("proposed_position")


def my_training(user):
    """Your own training, whether or not you may manage training for others."""
    from vertex.models import TrainingRecommendation

    me = employee_of(user)
    return TrainingRecommendation.objects.filter(employee=me) if me else TrainingRecommendation.objects.none()


def sees_all_training(user):
    return user.has_perm("vertex.view_alltraining")


def visible_training(user):
    from django.db.models import Q

    from vertex.models import TrainingRecommendation

    if not user.has_perm("vertex.view_trainingrecommendation"):
        return TrainingRecommendation.objects.none()
    training = TrainingRecommendation.objects.select_related("employee", "created_by")
    if sees_all_training(user):
        return training
    me = employee_of(user)
    if me is None:
        return training.none()
    return training.filter(Q(employee=me) | Q(employee__employee_work_info__reporting_manager_id=me))


def training_employees(user):
    from employee.models import Employee

    if not user.has_perm("vertex.add_trainingrecommendation"):
        return Employee.objects.none()
    if sees_all_training(user):
        return Employee.objects.filter(is_active=True)
    return reports_of(user)


def can_edit_training(user, training):
    return user.has_perm("vertex.change_trainingrecommendation") and (
        sees_all_training(user) or is_manager_of(user, training.employee)
    )


def can_update_training_status(user, training):
    me = employee_of(user)
    owner = me is not None and training.employee_id == me.pk
    return user.has_perm("vertex.view_trainingrecommendation") and (owner or can_edit_training(user, training))


def can_delete_training(user, training):
    return user.has_perm("vertex.delete_trainingrecommendation") and (
        sees_all_training(user) or is_manager_of(user, training.employee)
    )


# ---------------------------------------------------------------------------
# Expenses
# ---------------------------------------------------------------------------


def sees_all_expenses(user):
    return user.has_perm("vertex.view_allexpenses") or user.has_perm("vertex.change_expenseapproval")


def visible_expenses(user):
    """Everyone's for accountants / approvers / HR; otherwise only the ones you raised."""
    from vertex.models import Expense

    if not user.has_perm("vertex.view_expense"):
        return Expense.objects.none()
    expenses = Expense.objects.select_related("raised_by", "approver", "paid_by")
    if sees_all_expenses(user):
        return expenses
    me = employee_of(user)
    return expenses.filter(raised_by=me) if me else expenses.none()


def _mine_or_all(user, expense):
    me = employee_of(user)
    return sees_all_expenses(user) or (me is not None and expense.raised_by_id == me.pk)


def can_raise_expense(user):
    return user.has_perm("vertex.add_expense") and employee_of(user) is not None


def can_approve_expense(user, expense):
    """An approver decides a pending request that needs approval, never one they raised themselves."""
    from vertex.models import ExpenseStatus

    me = employee_of(user)
    return bool(
        user.has_perm("vertex.change_expenseapproval")
        and me is not None
        and expense.approval_required
        and expense.status == ExpenseStatus.PAYMENT_REQUESTED
        and expense.raised_by_id != me.pk
    )


def can_pay_expense(user, expense):
    """Paid once approved, or at once when no approval is needed; by whoever raised it (or an accountant)."""
    from vertex.models import ExpenseStatus

    ready = (expense.approval_required and expense.status == ExpenseStatus.APPROVED) or (
        not expense.approval_required and expense.status == ExpenseStatus.PAYMENT_REQUESTED
    )
    return bool(user.has_perm("vertex.change_expense") and ready and _mine_or_all(user, expense))


def can_upload_invoice(user, expense):
    from vertex.models import ExpenseStatus

    return bool(
        user.has_perm("vertex.change_expense")
        and expense.status in (ExpenseStatus.PAYMENT_REQUESTED, ExpenseStatus.APPROVED)
        and _mine_or_all(user, expense)
    )


def can_download_invoice(user, expense):
    return bool(expense.invoice and user.has_perm("vertex.view_expense") and _mine_or_all(user, expense))


def expense_summary(user):
    """Counts and totals for the expenses this person sees (the strip above the list)."""
    from django.db.models import Count, Q, Sum

    from vertex.models import ExpenseStatus

    expenses = visible_expenses(user)
    # the names must differ from the model's own fields, or Django cannot compute Sum("amount")
    return expenses.aggregate(
        expense_count=Count("pk"),
        pending_count=Count("pk", filter=Q(status=ExpenseStatus.PAYMENT_REQUESTED, approval_required=True)),
        paid_count=Count("pk", filter=Q(status=ExpenseStatus.PAID)),
        total_amount=Sum("amount"),
        paid_amount=Sum("amount", filter=Q(status=ExpenseStatus.PAID)),
    )
