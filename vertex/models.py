"""
vertex/models.py

Ariel Vertex projects, ported from ArielVertex (Project, ProjectMember, ProjectDocument,
ProjectComment, ProjectCall). ArielVertex is the source of truth.

Access is plain Ariel HR Groups and Permissions. Every model gets the standard
view/add/change/delete permissions, which appear in the existing permission grid:

* view_project         see the Vertex Projects pages (only projects the user is a member of)
* view_allprojects     see every project   (proxy model "Project - all projects")
* add_project          create projects
* change_project       edit a project the user manages (PM / PC on it)
* change_allprojects   edit any project
* add/change/delete_projectmember, add/delete_projectdocument, add_projectcall ...

Superusers (admin) always pass every permission check, as in the rest of Ariel HR.
"""

import os
from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


class ProjectStatus(models.TextChoices):
    PLANNING = "planning", _("Planning")
    ACTIVE = "active", _("Active")
    ON_HOLD = "on_hold", _("On hold")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class ProjectHealth(models.TextChoices):
    GREEN = "green", _("Green")
    AMBER = "amber", _("Amber")
    RED = "red", _("Red")


class Priority(models.IntegerChoices):
    LOW = 0, _("Low")
    MEDIUM = 1, _("Medium")
    HIGH = 2, _("High")
    CRITICAL = 3, _("Critical")


class ProjectRole(models.TextChoices):
    """Role a person holds on one specific project."""

    PROJECT_MANAGER = "project_manager", _("Project Manager")
    PROJECT_COORDINATOR = "project_coordinator", _("Project Coordinator")
    TECHNICAL_LEAD = "technical_lead", _("Technical Lead")
    BUSINESS_PERSON = "business_person", _("Business Person")
    DEVELOPER = "developer", _("Developer")
    QA = "qa", _("QA")
    MEMBER = "member", _("Member")


class DocumentCategory(models.TextChoices):
    REQUIREMENT = "requirement", _("Requirement")
    TECHNICAL = "technical", _("Technical")
    DEMO = "demo", _("Demo")
    CUSTOMER_CALL_RECORDING = "customer_call_recording", _("Customer call recording")
    MEETING_NOTES = "meeting_notes", _("Meeting notes")
    DEPLOYMENT = "deployment", _("Deployment")
    SCOPE = "scope", _("Scope")
    OTHER = "other", _("Other")


class Visibility(models.IntegerChoices):
    """Who may see a document. Ascending = more open."""

    INTERNAL = 0, _("Internal")
    BUSINESS_VISIBLE = 1, _("Business visible")
    CLIENT_SHAREABLE = 2, _("Client shareable")


class CallType(models.TextChoices):
    CUSTOMER = "customer", _("Customer")
    INTERNAL = "internal", _("Internal")


class Project(models.Model):
    """Project master record and root of the project workspace."""

    code = models.CharField(max_length=30, unique=True, verbose_name=_("Code"))
    name = models.CharField(max_length=200, verbose_name=_("Name"))
    description = models.TextField(blank=True, default="", verbose_name=_("Description"))
    status = models.CharField(
        max_length=20, choices=ProjectStatus.choices, default=ProjectStatus.ACTIVE
    )
    health = models.CharField(
        max_length=10, choices=ProjectHealth.choices, default=ProjectHealth.GREEN
    )
    priority = models.IntegerField(choices=Priority.choices, default=Priority.MEDIUM)
    start_date = models.DateField(verbose_name=_("Start date"))
    expected_end_date = models.DateField(
        null=True, blank=True, verbose_name=_("Expected end date")
    )
    client_name = models.CharField(max_length=200, blank=True, default="")
    business_owner = models.CharField(max_length=200, blank=True, default="")
    tags = models.CharField(max_length=300, blank=True, default="", help_text=_("Comma separated"))
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "employee.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Project")
        verbose_name_plural = _("Projects")
        ordering = ["-priority", "name"]

    def __str__(self):
        return f"{self.code} - {self.name}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-project", args=[self.pk])

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-project-edit", args=[self.pk])

    def redirect(self):
        """onclick attribute that opens the project workspace (used by the list rows)."""
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_avatar(self):
        return f"https://ui-avatars.com/api/?name={self.code}&background=random&bold=true"

    def get_initials(self):
        words = [w for w in self.name.replace("-", " ").split() if w[:1].isalnum()]
        letters = "".join(w[0] for w in words[:2]) or self.code[:2]
        return letters.upper()

    def get_member_count(self):
        return sum(1 for m in self.members.all() if m.is_active)

    def get_health_html(self):
        from django.utils.html import format_html

        colour = {"green": "#16a34a", "amber": "#f59e0b", "red": "#dc2626"}.get(self.health, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_health_display(),
        )

    def get_card_view_subtitle(self):
        from django.utils.html import format_html

        return format_html(
            '<div class="my-2">{}: <span class="font-semibold">{}</span></div>'
            '<div class="mb-2">{}: {}</div>'
            "<div>{}: <span class=\"font-semibold\">{}</span> &middot; {}: {}</div>",
            _("Status"), self.get_status_display(),
            _("Health"), self.get_health_html(),
            _("Priority"), self.get_priority_display(),
            _("Team"), self.get_member_count(),
        )

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]


class AllProjects(Project):
    """
    Permission switch only: holding ``view_allprojects`` shows every project (not just the
    ones the user is a member of) and ``change_allprojects`` allows editing any project.
    A proxy so it appears as its own row in the permission grid. It has no table.
    """

    class Meta:
        proxy = True
        verbose_name = _("Project (all projects)")
        verbose_name_plural = _("Projects (all projects)")


class ProjectMember(models.Model):
    """Join of an employee to a project with their role and allocation."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_memberships"
    )
    role_on_project = models.CharField(
        max_length=30, choices=ProjectRole.choices, default=ProjectRole.DEVELOPER
    )
    allocation_pct = models.PositiveSmallIntegerField(default=100)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("Project member")
        verbose_name_plural = _("Project members")
        constraints = [
            models.UniqueConstraint(
                fields=["project", "employee"], name="vertex_one_membership_per_project"
            )
        ]

    def __str__(self):
        return f"{self.employee} on {self.project.code}"


def document_path(instance, filename):
    return f"vertex/projectdocument/file/{filename}"


class ProjectDocument(models.Model):
    """A project file or video link. Downloads go through an authorised view."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    category = models.CharField(
        max_length=30, choices=DocumentCategory.choices, default=DocumentCategory.OTHER
    )
    visibility = models.IntegerField(choices=Visibility.choices, default=Visibility.INTERNAL)
    is_video_link = models.BooleanField(default=False)
    video_url = models.URLField(blank=True, default="")
    file = models.FileField(upload_to=document_path, blank=True, null=True)
    file_name = models.CharField(max_length=255, blank=True, default="")
    size_bytes = models.BigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Project document")
        verbose_name_plural = _("Project documents")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def delete(self, *args, **kwargs):
        stored = self.file
        super().delete(*args, **kwargs)
        if stored:
            stored.delete(save=False)

    @property
    def display_name(self):
        return self.file_name or os.path.basename(self.file.name) if self.file else ""


class ProjectComment(models.Model):
    """Collaboration note visible to every member of the project."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Project comment")
        verbose_name_plural = _("Project comments")
        ordering = ["created_at"]


class ProjectCall(models.Model):
    """Customer or internal call scheduled from the project screen.

    The meeting link is generated by the company's meeting provider (Google Meet or Teams, set in
    Recruitment settings) through vertex/meetings.py; ``meeting_error`` says why it could not be.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="calls")
    title = models.CharField(max_length=200)
    agenda = models.TextField(blank=True, default="")
    call_type = models.CharField(
        max_length=10, choices=CallType.choices, default=CallType.CUSTOMER
    )
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    attendees = models.CharField(max_length=500, blank=True, default="", help_text=_("Comma separated emails"))
    meeting_url = models.URLField(max_length=2048, blank=True, default="")
    meeting_provider = models.CharField(max_length=20, blank=True, default="")
    meeting_object_id = models.PositiveIntegerField(null=True, blank=True)
    meeting_error = models.CharField(max_length=300, blank=True, default="")
    meeting_notes = models.TextField(blank=True, default="")
    follow_up_action_items = models.TextField(blank=True, default="")
    scheduled_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Project call")
        verbose_name_plural = _("Project calls")
        ordering = ["-scheduled_at"]


# ---------------------------------------------------------------------------
# Phase 3: reviews and meeting minutes (ArielVertex ReviewRequest, ReviewMeeting,
# ProjectReview, CodeReview, MeetingMinute)
#
# * view_reviewrequest          see the Reviews page (own reviews: subject, reviewer or requester)
# * view_allreviewrequests      see every review request    (proxy "Review request (all)")
# * add_reviewrequest           request / schedule reviews  (also sees all requests, as in Vertex)
# * change_reviewrequest        schedule a request (and be PM/PC of its project)
# * add_projectreview / add_codereview   submit the outcome (assigned reviewer, or see-all)
# * view/add/change/delete_meetingminute minutes; only the person who captured them can touch them
# ---------------------------------------------------------------------------


class ReviewType(models.TextChoices):
    PROJECT_REVIEW = "project_review", _("Project review")
    CODE_REVIEW = "code_review", _("Code review")
    GENERAL_FEEDBACK = "general_feedback", _("General feedback")


def _when(value):
    """Readable local date and time for list columns, '-' when empty."""
    from django.utils import timezone

    if not value:
        return "-"
    return timezone.localtime(value).strftime("%d %b %Y, %H:%M")


class ReviewRequestStatus(models.TextChoices):
    OPEN = "open", _("Open")
    SCHEDULED = "scheduled", _("Scheduled")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class MeetingResponse(models.TextChoices):
    NO_RESPONSE = "no_response", _("No response")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")
    TENTATIVE = "tentative", _("Tentative")


class ReviewRequest(models.Model):
    """HR / management request for a project, code or general review of one employee."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="review_requests")
    subject = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_reviews_about"
    )
    requested_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="vertex_reviews_requested"
    )
    assigned_to = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vertex_reviews_assigned",
    )
    review_type = models.CharField(
        max_length=20, choices=ReviewType.choices, default=ReviewType.PROJECT_REVIEW
    )
    status = models.CharField(
        max_length=15, choices=ReviewRequestStatus.choices, default=ReviewRequestStatus.OPEN
    )
    notes = models.TextField(blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Review request")
        verbose_name_plural = _("Review requests")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_review_type_display()} of {self.subject} ({self.project.code})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-review", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_project_url(self):
        return self.project.get_absolute_url()

    def get_project_code(self):
        return self.project.code

    def get_subject_name(self):
        return self.subject.get_full_name()

    def get_reviewer_name(self):
        return self.assigned_to.get_full_name() if self.assigned_to else "-"

    def get_meeting_time(self):
        meeting = getattr(self, "meeting", None)
        return _when(meeting.scheduled_at) if meeting else "-"

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {
            "open": "#3b82f6", "scheduled": "#8b5cf6", "in_progress": "#f59e0b",
            "completed": "#16a34a", "cancelled": "#6b7280",
        }.get(self.status, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )


class AllReviewRequests(ReviewRequest):
    """Permission switch only: ``view_allreviewrequests`` shows every review request."""

    class Meta:
        proxy = True
        verbose_name = _("Review request (all)")
        verbose_name_plural = _("Review requests (all)")


class ReviewMeeting(models.Model):
    """The scheduled review meeting. Its link is generated by the company's meeting provider (vertex/meetings.py)."""

    review_request = models.OneToOneField(
        ReviewRequest, on_delete=models.CASCADE, related_name="meeting"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    scheduled_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    attendees = models.CharField(max_length=1000, blank=True, default="")
    meeting_url = models.URLField(max_length=2048, blank=True, default="")
    meeting_provider = models.CharField(max_length=20, blank=True, default="")
    meeting_object_id = models.PositiveIntegerField(null=True, blank=True)
    meeting_error = models.CharField(max_length=300, blank=True, default="")
    response_status = models.CharField(
        max_length=15, choices=MeetingResponse.choices, default=MeetingResponse.NO_RESPONSE
    )
    responded_at = models.DateTimeField(null=True, blank=True)
    scheduled_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        verbose_name = _("Review meeting")
        verbose_name_plural = _("Review meetings")


_RATING = [MinValueValidator(1), MaxValueValidator(5)]


class ProjectReview(models.Model):
    """Project contribution review submitted by the assigned reviewer (PM / PC)."""

    review_request = models.OneToOneField(
        ReviewRequest, on_delete=models.CASCADE, related_name="project_review"
    )
    reviewer = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    delivery_rating = models.PositiveSmallIntegerField(validators=_RATING)
    ownership_rating = models.PositiveSmallIntegerField(validators=_RATING)
    collaboration_rating = models.PositiveSmallIntegerField(validators=_RATING)
    strengths = models.TextField(blank=True, default="")
    observations = models.TextField(blank=True, default="")
    action_items = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Project review")
        verbose_name_plural = _("Project reviews")


class CodeReview(models.Model):
    """Technical review outcome captured by a Technical Lead."""

    review_request = models.OneToOneField(
        ReviewRequest, on_delete=models.CASCADE, related_name="code_review"
    )
    reviewer = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    code_quality_rating = models.PositiveSmallIntegerField(validators=_RATING)
    architecture_rating = models.PositiveSmallIntegerField(validators=_RATING)
    testing_rating = models.PositiveSmallIntegerField(validators=_RATING)
    strengths = models.TextField(blank=True, default="")
    observations = models.TextField(blank=True, default="")
    blockers = models.TextField(blank=True, default="")
    action_items = models.TextField(blank=True, default="")
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Code review")
        verbose_name_plural = _("Code reviews")


class MinuteStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    PREVIEW = "preview", _("Preview")
    APPROVED = "approved", _("Approved")
    SENT = "sent", _("Sent")


class MeetingMinute(models.Model):
    """
    Notes captured -> minutes generated -> previewed -> approved -> sent to attendees.
    Editing approved or sent minutes sends them back to Preview so they are approved again.
    """

    title = models.CharField(max_length=200)
    meeting_date = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True, default="")
    attendees = models.TextField(blank=True, default="", help_text=_("Emails, comma or line separated"))
    raw_notes = models.TextField(blank=True, default="")
    minutes_text = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=MinuteStatus.choices, default=MinuteStatus.DRAFT)
    created_by = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_minutes"
    )
    approved_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Meeting minute")
        verbose_name_plural = _("Meeting minutes")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-minute", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-minute-edit", args=[self.pk])

    def get_delete_url(self):
        from django.urls import reverse

        return reverse("vertex-minute-delete", args=[self.pk])

    def get_meeting_when(self):
        return _when(self.meeting_date)

    def get_sent_display(self):
        return f"{_when(self.sent_at)} ({self.sent_count}x)" if self.sent_at else "-"

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {
            "draft": "#6b7280", "preview": "#8b5cf6", "approved": "#16a34a", "sent": "#0ea5e9",
        }.get(self.status, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )


# ---------------------------------------------------------------------------
# Status updates (ArielVertex StatusUpdate): a developer daily / weekly update on a project
# the person is assigned to.
#
# * view_statusupdate        see the Status Updates page (own updates) and the project tab
# * add_statusupdate         submit an update (only for projects the person is a member of)
# * view_allstatusupdates    see internal notes and the consolidated summary (proxy "Status update (all)")
# ---------------------------------------------------------------------------


class UpdateStatus(models.TextChoices):
    ON_TRACK = "on_track", _("On track")
    AT_RISK = "at_risk", _("At risk")
    BLOCKED = "blocked", _("Blocked")


class StatusUpdate(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="status_updates")
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_status_updates"
    )
    update_date = models.DateField(verbose_name=_("Date"))
    work_completed = models.TextField(verbose_name=_("Work completed"), max_length=4000)
    next_planned_work = models.TextField(blank=True, default="", verbose_name=_("Next planned work"))
    blockers = models.TextField(blank=True, default="")
    dependencies = models.TextField(blank=True, default="")
    hours_spent = models.DecimalField(
        max_digits=4, decimal_places=1, default=8,
        validators=[MinValueValidator(0), MaxValueValidator(24)], verbose_name=_("Hours"),
    )
    status = models.CharField(
        max_length=10, choices=UpdateStatus.choices, default=UpdateStatus.ON_TRACK
    )
    internal_note = models.TextField(
        blank=True, default="", verbose_name=_("Internal note"),
        help_text=_("Never shown to business people."),
    )
    client_shareable_summary = models.TextField(
        blank=True, default="", max_length=2000, verbose_name=_("Client-shareable summary"),
        help_text=_("The only part rolled into the business summary."),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Status update")
        verbose_name_plural = _("Status updates")
        ordering = ["-update_date", "-created_at"]

    def __str__(self):
        return f"{self.employee} - {self.project.code} - {self.update_date}"

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {"on_track": "#16a34a", "at_risk": "#f59e0b", "blocked": "#dc2626"}.get(
            self.status, "#6b7280"
        )
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )

    def get_project_code(self):
        return self.project.code

    def get_project_url(self):
        return self.project.get_absolute_url()


class AllStatusUpdates(StatusUpdate):
    """Permission switch only: see internal notes and the consolidated summary."""

    class Meta:
        proxy = True
        verbose_name = _("Status update (all)")
        verbose_name_plural = _("Status updates (all)")


# ---------------------------------------------------------------------------
# Resources (ArielVertex ResourcesController + ResourceRequestsController)
#
# * view_resourcerequest        see the Hiring Requests page (own requests)
# * view_allresourcerequests    see every request     (proxy "Resource request (all)")
# * add_resourcerequest         raise a request for a project the person can see
# * change_resourcerequest      move a request through its status flow (HR); also sees all
# * view_resourcevisibility     the Resource Visibility page (allocation of every person)
# ---------------------------------------------------------------------------


class ResourceRequestStatus(models.TextChoices):
    OPEN = "open", _("Open")
    UNDER_REVIEW = "under_review", _("Under review")
    NEED_MORE_INFORMATION = "need_more_information", _("Need more information")
    APPROVED = "approved", _("Approved")
    IN_HIRING_PROCESS = "in_hiring_process", _("In hiring process")
    CANDIDATE_SHARED = "candidate_shared", _("Candidate shared")
    FULFILLED = "fulfilled", _("Fulfilled")
    REJECTED = "rejected", _("Rejected")
    CLOSED = "closed", _("Closed")


class ResourceRequest(models.Model):
    """Hiring / resource request raised by a project manager or coordinator."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="resource_requests")
    requested_by = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="vertex_resource_requests"
    )
    role_title = models.CharField(max_length=200, verbose_name=_("Role title"))
    skills = models.CharField(max_length=500, blank=True, default="", help_text=_("Comma separated"))
    reason = models.TextField(blank=True, default="")
    priority = models.IntegerField(choices=Priority.choices, default=Priority.MEDIUM)
    count = models.PositiveSmallIntegerField(
        default=1, validators=[MinValueValidator(1), MaxValueValidator(100)], verbose_name=_("Count")
    )
    expected_start_date = models.DateField(null=True, blank=True, verbose_name=_("Expected start"))
    status = models.CharField(
        max_length=25, choices=ResourceRequestStatus.choices, default=ResourceRequestStatus.OPEN
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Resource request")
        verbose_name_plural = _("Resource requests")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.role_title} x{self.count} ({self.project.code})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-resource-request", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_project_url(self):
        return self.project.get_absolute_url()

    def get_project_code(self):
        return self.project.code

    def get_requested_by_name(self):
        return self.requested_by.get_full_name() if self.requested_by else "-"

    def get_raised_on(self):
        return _when(self.created_at)

    def get_status_html(self):
        from django.utils.html import format_html

        colour = {
            "open": "#3b82f6", "under_review": "#8b5cf6", "need_more_information": "#f59e0b",
            "approved": "#16a34a", "in_hiring_process": "#0ea5e9", "candidate_shared": "#14b8a6",
            "fulfilled": "#15803d", "rejected": "#dc2626", "closed": "#6b7280",
        }.get(self.status, "#6b7280")
        return format_html(
            '<span style="display:inline-flex;align-items:center;gap:6px">'
            '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
            colour,
            self.get_status_display(),
        )


class AllResourceRequests(ResourceRequest):
    """Permission switch only: ``view_allresourcerequests`` shows every request."""

    class Meta:
        proxy = True
        verbose_name = _("Resource request (all)")
        verbose_name_plural = _("Resource requests (all)")


class ResourceRequestComment(models.Model):
    resource_request = models.ForeignKey(
        ResourceRequest, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        "employee.Employee", null=True, on_delete=models.SET_NULL, related_name="+"
    )
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Resource request comment")
        verbose_name_plural = _("Resource request comments")
        ordering = ["created_at"]


class ResourceVisibility(ProjectMember):
    """Permission switch only: ``view_resourcevisibility`` opens the Resource Visibility page."""

    class Meta:
        proxy = True
        verbose_name = _("Resource visibility")
        verbose_name_plural = _("Resource visibility")


# ---------------------------------------------------------------------------
# Performance: structured feedback, calculated performance reports, improvement plans
# (ArielVertex Feedback, PerformanceReport, Pip). Nothing is employee-visible until it
# has been approved / published.
#
# * view_feedback / add_feedback / change_feedback   author their own feedback
# * view_allfeedback                                  see every author's feedback   (proxy "Feedback (all)")
# * change_feedbackapproval                           request revision, approve, publish (proxy "Feedback approval")
# * view_performancereport / add_performancereport    open the page / generate a report
# * view_allperformancereports                        see every report, drafts included
# * change_performancepublication                     publish a report (proxy "Performance publication")
# * view_myperformance                                the employee "My Performance" page (proxy)
# * view_pip / add_pip / change_pip                   improvement plans
# ---------------------------------------------------------------------------


def _colour_html(colour, label):
    from django.utils.html import format_html

    return format_html(
        '<span style="display:inline-flex;align-items:center;gap:6px">'
        '<span style="width:9px;height:9px;border-radius:50%;background:{}"></span>{}</span>',
        colour,
        label,
    )


class FeedbackStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    REVISION_REQUESTED = "revision_requested", _("Revision requested")
    APPROVED = "approved", _("Approved")
    PUBLISHED = "published", _("Published")


class Feedback(models.Model):
    """Structured quarterly feedback about one employee, with an approval lifecycle."""

    subject = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_feedback_about"
    )
    author = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_feedback_written"
    )
    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.SET_NULL, related_name="feedback"
    )
    period = models.CharField(max_length=20, help_text=_("For example 2026-Q4"))
    status = models.CharField(
        max_length=20, choices=FeedbackStatus.choices, default=FeedbackStatus.DRAFT
    )
    constructive_summary = models.TextField(
        verbose_name=_("Summary for the employee"),
        help_text=_("Constructive and safe to publish: this is what the employee sees."),
    )
    strengths = models.TextField(blank=True, default="")
    improvement_areas = models.TextField(blank=True, default="")
    action_plan = models.TextField(blank=True, default="")
    internal_notes = models.TextField(
        blank=True, default="", help_text=_("Confidential management notes. Never shown to the employee.")
    )
    approved_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    revision_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Feedback")
        verbose_name_plural = _("Feedback")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Feedback on {self.subject} ({self.period})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-feedback-detail", args=[self.pk])

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-feedback-edit", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_subject_name(self):
        return self.subject.get_full_name()

    def get_author_name(self):
        return self.author.get_full_name()

    def get_project_code(self):
        return self.project.code if self.project else "-"

    def get_status_html(self):
        colour = {
            "draft": "#6b7280", "submitted": "#3b82f6", "revision_requested": "#f59e0b",
            "approved": "#8b5cf6", "published": "#16a34a",
        }.get(self.status, "#6b7280")
        return _colour_html(colour, self.get_status_display())

    def get_average_score(self):
        scores = [c.score for c in self.category_scores.all() if not c.not_applicable]
        return round(sum(scores) / len(scores)) if scores else None

    def get_average_display(self):
        average = self.get_average_score()
        return f"{average}%" if average is not None else "-"


class AllFeedback(Feedback):
    """Permission switch only: ``view_allfeedback`` shows every author's feedback."""

    class Meta:
        proxy = True
        verbose_name = _("Feedback (all)")
        verbose_name_plural = _("Feedback (all)")


class FeedbackApproval(Feedback):
    """Permission switch only: ``change_feedbackapproval`` may request revision, approve and publish."""

    class Meta:
        proxy = True
        verbose_name = _("Feedback approval")
        verbose_name_plural = _("Feedback approval")


class FeedbackCategoryScore(models.Model):
    feedback = models.ForeignKey(Feedback, on_delete=models.CASCADE, related_name="category_scores")
    category = models.CharField(max_length=30)
    score = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    not_applicable = models.BooleanField(default=False)
    comment = models.TextField(blank=True, default="")

    class Meta:
        verbose_name = _("Feedback category score")
        verbose_name_plural = _("Feedback category scores")
        constraints = [
            models.UniqueConstraint(fields=["feedback", "category"], name="vertex_one_score_per_category")
        ]


class PerformancePeriodType(models.TextChoices):
    MONTHLY = "monthly", _("Monthly")
    QUARTERLY = "quarterly", _("Quarterly")


class PerformanceRating(models.TextChoices):
    OUTSTANDING = "outstanding", _("Outstanding")
    EXCEEDS = "exceeds_expectations", _("Exceeds Expectations")
    MEETS = "meets_expectations", _("Meets Expectations")
    NEEDS_IMPROVEMENT = "needs_improvement", _("Needs Improvement")
    ATTENTION = "attention_required", _("Performance Attention Required")


class PerformanceReport(models.Model):
    """Calculated monthly / quarterly summary. Stays a draft until it is published."""

    subject = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_performance_reports"
    )
    period_type = models.CharField(
        max_length=10, choices=PerformancePeriodType.choices, default=PerformancePeriodType.MONTHLY
    )
    period = models.CharField(max_length=10, help_text=_("2026-07 or 2026-Q3"))
    overall_score = models.DecimalField(max_digits=4, decimal_places=1, default=0)
    rating = models.CharField(
        max_length=25, choices=PerformanceRating.choices, default=PerformanceRating.MEETS
    )
    strengths = models.TextField(blank=True, default="")
    improvement_areas = models.TextField(blank=True, default="")
    recommended_actions = models.TextField(blank=True, default="")
    data_sources = models.TextField(blank=True, default="")
    is_published = models.BooleanField(default=False)
    approved_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Performance report")
        verbose_name_plural = _("Performance reports")
        ordering = ["-period", "subject__employee_first_name"]
        constraints = [
            models.UniqueConstraint(fields=["subject", "period"], name="vertex_one_report_per_period")
        ]

    def __str__(self):
        return f"{self.subject} {self.period}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-performance-report", args=[self.pk])

    def redirect(self):
        return f"onclick=\"window.location.href='{self.get_absolute_url()}'\""

    def get_subject_name(self):
        return self.subject.get_full_name()

    def get_score_display(self):
        return f"{self.overall_score}%"

    def get_rating_html(self):
        colour = {
            "outstanding": "#15803d", "exceeds_expectations": "#16a34a", "meets_expectations": "#3b82f6",
            "needs_improvement": "#f59e0b", "attention_required": "#dc2626",
        }.get(self.rating, "#6b7280")
        return _colour_html(colour, self.get_rating_display())

    def get_published_html(self):
        if self.is_published:
            return _colour_html("#16a34a", _("Published"))
        return _colour_html("#6b7280", _("Draft"))


class AllPerformanceReports(PerformanceReport):
    """Permission switch only: ``view_allperformancereports`` shows every report, drafts included."""

    class Meta:
        proxy = True
        verbose_name = _("Performance report (all)")
        verbose_name_plural = _("Performance reports (all)")


class PerformancePublication(PerformanceReport):
    """Permission switch only: ``change_performancepublication`` may publish a report."""

    class Meta:
        proxy = True
        verbose_name = _("Performance publication")
        verbose_name_plural = _("Performance publication")


class MyPerformance(PerformanceReport):
    """Permission switch only: ``view_myperformance`` opens the employee's own performance page."""

    class Meta:
        proxy = True
        verbose_name = _("My performance")
        verbose_name_plural = _("My performance")


class PerformanceCategoryScore(models.Model):
    report = models.ForeignKey(PerformanceReport, on_delete=models.CASCADE, related_name="category_scores")
    category = models.CharField(max_length=30)
    score = models.PositiveSmallIntegerField(default=0)
    weight = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    not_applicable = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Performance category score")
        verbose_name_plural = _("Performance category scores")


class PipStatus(models.TextChoices):
    OPEN = "open", _("Open")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    CLOSED = "closed", _("Closed")


class PipOutcome(models.TextChoices):
    PENDING = "pending", _("Pending")
    IMPROVED = "improved", _("Improved")
    NOT_IMPROVED = "not_improved", _("Not improved")


class Pip(models.Model):
    """Performance Improvement Plan: created by HR, or automatically when a published score is low."""

    subject = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_pips"
    )
    reason = models.TextField()
    expected_improvement = models.TextField(blank=True, default="")
    support_provided = models.TextField(blank=True, default="")
    status = models.CharField(max_length=15, choices=PipStatus.choices, default=PipStatus.OPEN)
    outcome = models.CharField(max_length=15, choices=PipOutcome.choices, default=PipOutcome.PENDING)
    review_notes = models.TextField(blank=True, default="")
    start_date = models.DateField()
    is_auto = models.BooleanField(default=False)
    trigger_score = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    trigger_period = models.CharField(max_length=10, blank=True, default="")
    created_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Improvement plan")
        verbose_name_plural = _("Improvement plans")
        ordering = ["-created_at"]

    def __str__(self):
        return f"PIP for {self.subject}"

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-pip-edit", args=[self.pk])

    def get_subject_name(self):
        return self.subject.get_full_name()

    def get_source_display(self):
        return _("Automatic") if self.is_auto else _("Manual")

    def get_status_html(self):
        colour = {
            "open": "#3b82f6", "in_progress": "#f59e0b", "completed": "#16a34a", "closed": "#6b7280",
        }.get(self.status, "#6b7280")
        return _colour_html(colour, self.get_status_display())

    def get_outcome_html(self):
        colour = {"pending": "#6b7280", "improved": "#16a34a", "not_improved": "#dc2626"}.get(
            self.outcome, "#6b7280"
        )
        return _colour_html(colour, self.get_outcome_display())


class VertexSetting(models.Model):
    """Small key / value store for Vertex rules (for example the PIP threshold)."""

    key = models.CharField(max_length=100, unique=True)
    value = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        verbose_name = _("Vertex setting")
        verbose_name_plural = _("Vertex settings")

    def __str__(self):
        return f"{self.key} = {self.value}"


# ---------------------------------------------------------------------------
# Appraisals: cycles, appraisals (self -> manager -> release) and goals
#
# * view_appraisalcycle / add_appraisalcycle / change_appraisalcycle   HR runs the cycles
# * view_appraisal      your own appraisals, plus your reports' when you may evaluate
# * change_appraisal    evaluate the appraisals of the people who report to you
# * view_allappraisals  every appraisal                  (proxy "Appraisals - all")
# * change_appraisalrelease  release the final rating    (proxy "Appraisal release")
# * view_goal / add_goal / change_goal / delete_goal     goals and KRAs
# * view_allgoals       every goal                       (proxy "Goals - all")
# ---------------------------------------------------------------------------


class CycleStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    ACTIVE = "active", _("Active")
    CLOSED = "closed", _("Closed")


class AppraisalCycle(models.Model):
    """An appraisal round, for example "H1 2027 appraisal"; HR activates it to open every appraisal."""

    name = models.CharField(max_length=150, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=CycleStatus.choices, default=CycleStatus.DRAFT)
    created_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Appraisal cycle")
        verbose_name_plural = _("Appraisal cycles")
        ordering = ["-start_date", "-id"]

    def __str__(self):
        return self.name

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-cycle-edit", args=[self.pk])

    def get_appraisals_url(self):
        from django.urls import reverse

        return f"{reverse('vertex-appraisals')}?cycle={self.pk}"

    def get_activate_url(self):
        from django.urls import reverse

        return reverse("vertex-cycle-activate", args=[self.pk])

    def get_close_url(self):
        from django.urls import reverse

        return reverse("vertex-cycle-close", args=[self.pk])

    def get_period_display(self):
        return f"{self.start_date:%d %b %Y} - {self.end_date:%d %b %Y}"

    def get_progress_display(self):
        total = self.appraisals.count()
        if not total:
            return "-"
        return f"{self.appraisals.filter(stage=AppraisalStage.RELEASED).count()} / {total} released"

    def get_status_html(self):
        colour = {"draft": "#6b7280", "active": "#16a34a", "closed": "#475569"}.get(self.status, "#6b7280")
        return _colour_html(colour, self.get_status_display())


class AppraisalStage(models.TextChoices):
    SELF_PENDING = "self_pending", _("Self assessment pending")
    SELF_SUBMITTED = "self_submitted", _("Awaiting manager")
    MANAGER_COMPLETED = "manager_completed", _("Awaiting release")
    RELEASED = "released", _("Released")


RATING_VALIDATORS = [MinValueValidator(1), MaxValueValidator(5)]


class Appraisal(models.Model):
    """One person's appraisal in one cycle: self assessment, manager evaluation, then HR releases it."""

    cycle = models.ForeignKey(AppraisalCycle, on_delete=models.CASCADE, related_name="appraisals")
    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_appraisals"
    )
    manager = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vertex_appraisals_to_evaluate",
    )
    stage = models.CharField(max_length=20, choices=AppraisalStage.choices, default=AppraisalStage.SELF_PENDING)

    self_rating = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True, validators=RATING_VALIDATORS)
    self_comments = models.TextField(blank=True, default="")
    self_submitted_at = models.DateTimeField(null=True, blank=True)

    manager_rating = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True, validators=RATING_VALIDATORS)
    manager_comments = models.TextField(blank=True, default="")
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)

    final_rating = models.DecimalField(max_digits=2, decimal_places=1, null=True, blank=True, validators=RATING_VALIDATORS)
    final_comments = models.TextField(blank=True, default="")
    released_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Appraisal")
        verbose_name_plural = _("Appraisals")
        ordering = ["-cycle__start_date", "employee__employee_first_name"]
        constraints = [
            models.UniqueConstraint(fields=["cycle", "employee"], name="vertex_one_appraisal_per_cycle")
        ]

    def __str__(self):
        return f"{self.employee} - {self.cycle}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-appraisal", args=[self.pk])

    def get_self_url(self):
        from django.urls import reverse

        return reverse("vertex-appraisal-self", args=[self.pk])

    def get_manager_url(self):
        from django.urls import reverse

        return reverse("vertex-appraisal-manager", args=[self.pk])

    def get_release_url(self):
        from django.urls import reverse

        return reverse("vertex-appraisal-release", args=[self.pk])

    def get_employee_name(self):
        return self.employee.get_full_name()

    def get_manager_name(self):
        return self.manager.get_full_name() if self.manager_id else "-"

    def get_cycle_name(self):
        return self.cycle.name

    def get_self_rating_display(self):
        return f"{self.self_rating} / 5" if self.self_rating is not None else "-"

    def get_final_rating_display(self):
        return f"{self.final_rating} / 5" if self.final_rating is not None else "-"

    def get_stage_html(self):
        colour = {
            "self_pending": "#6b7280", "self_submitted": "#3b82f6",
            "manager_completed": "#f59e0b", "released": "#16a34a",
        }.get(self.stage, "#6b7280")
        return _colour_html(colour, self.get_stage_display())


class AllAppraisals(Appraisal):
    """Permission switch only: ``view_allappraisals`` shows everyone's appraisals."""

    class Meta:
        proxy = True
        verbose_name = _("Appraisals (all)")
        verbose_name_plural = _("Appraisals (all)")


class AppraisalRelease(Appraisal):
    """Permission switch only: ``change_appraisalrelease`` may release the final rating."""

    class Meta:
        proxy = True
        verbose_name = _("Appraisal release")
        verbose_name_plural = _("Appraisal release")


class GoalStatus(models.TextChoices):
    NOT_STARTED = "not_started", _("Not started")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")
    CANCELLED = "cancelled", _("Cancelled")


class Goal(models.Model):
    """A goal / KRA for one employee with a weight (share of the whole) and a progress percentage."""

    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_goals"
    )
    assigned_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cycle = models.ForeignKey(
        AppraisalCycle, null=True, blank=True, on_delete=models.SET_NULL, related_name="goals"
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=80, blank=True, default="", help_text=_("KRA category"))
    weightage = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text=_("Share of the whole, in percent. Active goals of one person in one cycle add up to at most 100."),
    )
    progress = models.PositiveSmallIntegerField(default=0, validators=[MaxValueValidator(100)])
    target_date = models.DateField()
    status = models.CharField(max_length=15, choices=GoalStatus.choices, default=GoalStatus.NOT_STARTED)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Goal")
        verbose_name_plural = _("Goals")
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-goal-edit", args=[self.pk])

    def get_progress_url(self):
        from django.urls import reverse

        return reverse("vertex-goal-progress", args=[self.pk])

    def get_delete_url(self):
        from django.urls import reverse

        return reverse("vertex-goal-delete", args=[self.pk])

    def get_employee_name(self):
        return self.employee.get_full_name()

    def get_cycle_name(self):
        return self.cycle.name if self.cycle_id else "-"

    def get_weight_display(self):
        return f"{self.weightage}%"

    def get_progress_display(self):
        return f"{self.progress}%"

    def get_status_html(self):
        colour = {
            "not_started": "#6b7280", "in_progress": "#f59e0b", "completed": "#16a34a", "cancelled": "#dc2626",
        }.get(self.status, "#6b7280")
        return _colour_html(colour, self.get_status_display())


class AllGoals(Goal):
    """Permission switch only: ``view_allgoals`` shows everyone's goals."""

    class Meta:
        proxy = True
        verbose_name = _("Goals (all)")
        verbose_name_plural = _("Goals (all)")


# ---------------------------------------------------------------------------
# Promotions and learning (ArielVertex Promotions / Learning controllers)
#
# * view_promotion / add_promotion   your own outcome and your reports' requests / recommend a promotion
# * view_allpromotions               every promotion                    (proxy "Promotions - all")
# * change_promotionvalidation       HR: validate, complete, reject     (proxy "Promotion validation")
# * change_promotionapproval         leadership: approve, reject        (proxy "Promotion approval")
# * view/add/change/delete_trainingrecommendation   training for your reports (or your own, read)
# * view_alltraining                 everyone's training                (proxy "Training - all")
# ---------------------------------------------------------------------------


class PromotionStage(models.TextChoices):
    RECOMMENDED = "recommended", _("Recommended")
    HR_VALIDATED = "hr_validated", _("HR validated")
    APPROVED = "approved", _("Leadership approved")
    COMPLETED = "completed", _("Completed")
    REJECTED = "rejected", _("Rejected")


OPEN_PROMOTION_STAGES = (
    PromotionStage.RECOMMENDED, PromotionStage.HR_VALIDATED, PromotionStage.APPROVED,
)


class Promotion(models.Model):
    """A promotion moving Manager -> HR -> Leadership -> HR completes it (or anyone with the right rejects it)."""

    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_promotions"
    )
    recommended_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    current_designation = models.CharField(max_length=100, blank=True, default="")
    proposed_position = models.ForeignKey(
        "base.JobPosition", on_delete=models.PROTECT, related_name="+", verbose_name=_("Proposed designation")
    )
    current_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    proposed_salary = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    justification = models.TextField()
    stage = models.CharField(max_length=15, choices=PromotionStage.choices, default=PromotionStage.RECOMMENDED)
    decision_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("Promotion")
        verbose_name_plural = _("Promotions")
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["employee"],
                condition=models.Q(stage__in=["recommended", "hr_validated", "approved"]),
                name="vertex_one_open_promotion_per_employee",
            )
        ]

    def __str__(self):
        return f"{self.employee} -> {self.proposed_position}"

    @property
    def is_open(self):
        return self.stage in OPEN_PROMOTION_STAGES

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-promotion", args=[self.pk])

    def get_employee_name(self):
        return self.employee.get_full_name()

    def get_recommender_name(self):
        return self.recommended_by.get_full_name() if self.recommended_by_id else "-"

    def get_designation_display(self):
        return f"{self.current_designation or '-'} -> {self.proposed_position.job_position}"

    def get_stage_html(self):
        colour = {
            "recommended": "#3b82f6", "hr_validated": "#8b5cf6", "approved": "#f59e0b",
            "completed": "#16a34a", "rejected": "#dc2626",
        }.get(self.stage, "#6b7280")
        return _colour_html(colour, self.get_stage_display())

    def get_validate_url(self):
        from django.urls import reverse

        return reverse("vertex-promotion-validate", args=[self.pk])

    def get_approve_url(self):
        from django.urls import reverse

        return reverse("vertex-promotion-approve", args=[self.pk])

    def get_complete_url(self):
        from django.urls import reverse

        return reverse("vertex-promotion-complete", args=[self.pk])

    def get_reject_url(self):
        from django.urls import reverse

        return reverse("vertex-promotion-reject", args=[self.pk])


class PromotionStep(models.Model):
    """One row for every stage change: who moved it, when, and what they wrote."""

    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name="steps")
    from_stage = models.CharField(max_length=15, blank=True, default="")
    to_stage = models.CharField(max_length=15, choices=PromotionStage.choices)
    actor = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    note = models.TextField(blank=True, default="")
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["at", "id"]


class AllPromotions(Promotion):
    """Permission switch only: ``view_allpromotions`` shows every promotion."""

    class Meta:
        proxy = True
        verbose_name = _("Promotions (all)")
        verbose_name_plural = _("Promotions (all)")


class PromotionValidation(Promotion):
    """Permission switch only: ``change_promotionvalidation`` may validate, complete and reject (HR)."""

    class Meta:
        proxy = True
        verbose_name = _("Promotion validation")
        verbose_name_plural = _("Promotion validation")


class PromotionApproval(Promotion):
    """Permission switch only: ``change_promotionapproval`` may approve and reject (leadership)."""

    class Meta:
        proxy = True
        verbose_name = _("Promotion approval")
        verbose_name_plural = _("Promotion approval")


class TrainingStatus(models.TextChoices):
    RECOMMENDED = "recommended", _("Recommended")
    IN_PROGRESS = "in_progress", _("In progress")
    COMPLETED = "completed", _("Completed")


class TrainingSource(models.TextChoices):
    MANUAL = "manual", _("Manual")
    SUGGESTED = "suggested", _("Suggested")


class TrainingRecommendation(models.Model):
    """Training for one employee, assigned by hand or suggested from goal progress and performance scores."""

    employee = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_training"
    )
    skill_gap = models.CharField(max_length=200)
    recommended_training = models.CharField(max_length=200)
    duration_months = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(24)]
    )
    status = models.CharField(max_length=15, choices=TrainingStatus.choices, default=TrainingStatus.RECOMMENDED)
    source = models.CharField(max_length=10, choices=TrainingSource.choices, default=TrainingSource.MANUAL)
    created_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Training recommendation")
        verbose_name_plural = _("Training recommendations")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.employee}: {self.recommended_training}"

    def get_status_url(self):
        from django.urls import reverse

        return reverse("vertex-training-status", args=[self.pk])

    def get_update_url(self):
        from django.urls import reverse

        return reverse("vertex-training-edit", args=[self.pk])

    def get_delete_url(self):
        from django.urls import reverse

        return reverse("vertex-training-delete", args=[self.pk])

    def get_employee_name(self):
        return self.employee.get_full_name()

    def get_duration_display(self):
        return _("%(n)s month(s)") % {"n": self.duration_months}

    def get_status_html(self):
        colour = {"recommended": "#3b82f6", "in_progress": "#f59e0b", "completed": "#16a34a"}.get(
            self.status, "#6b7280"
        )
        return _colour_html(colour, self.get_status_display())


class AllTraining(TrainingRecommendation):
    """Permission switch only: ``view_alltraining`` shows everyone's training."""

    class Meta:
        proxy = True
        verbose_name = _("Training (all)")
        verbose_name_plural = _("Training (all)")


# ---------------------------------------------------------------------------
# Expenses (ArielVertex Expenses controller): front desk raises a payment request, an approver
# decides when approval is needed, the person who raised it marks it paid.
#
# * view_expense / add_expense        your own expenses / raise one
# * change_expense                    upload an invoice and mark your own expense paid
# * view_allexpenses                  every expense                      (proxy "Expenses - all")
# * change_expenseapproval            approve or reject                  (proxy "Expense approval")
# * view_expensesetting / change_expensesetting   the expense settings screen
# ---------------------------------------------------------------------------


class ExpenseCategory(models.TextChoices):
    OFFICE_SUPPLIES = "office_supplies", _("Office supplies")
    UTILITIES = "utilities", _("Utilities")
    TRAVEL = "travel", _("Travel")
    REFRESHMENTS = "refreshments", _("Refreshments")
    MAINTENANCE = "maintenance", _("Maintenance")
    SOFTWARE = "software", _("Software")
    COURIER = "courier", _("Courier")
    OTHER = "other", _("Other")


class ExpenseStatus(models.TextChoices):
    PAYMENT_REQUESTED = "payment_requested", _("Payment requested")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    PAID = "paid", _("Paid")


class PaymentMethod(models.TextChoices):
    CASH = "cash", _("Cash")
    UPI = "upi", _("UPI")
    CARD = "card", _("Card")
    BANK_TRANSFER = "bank_transfer", _("Bank transfer")
    CHEQUE = "cheque", _("Cheque")
    OTHER = "other", _("Other")


def invoice_path(instance, filename):
    import uuid

    extension = os.path.splitext(filename)[1].lower()
    return f"vertex/expense/invoice/{uuid.uuid4().hex}{extension}"


class Expense(models.Model):
    """One payment request. Amounts are in rupees."""

    title = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    category = models.CharField(max_length=20, choices=ExpenseCategory.choices, default=ExpenseCategory.OTHER)
    amount = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01")), MaxValueValidator(Decimal("99999999.99"))],
    )
    vendor = models.CharField(max_length=120, blank=True, default="")
    expense_date = models.DateField()
    payment_method = models.CharField(max_length=15, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    invoice_number = models.CharField(max_length=60, blank=True, default="")
    invoice = models.FileField(upload_to=invoice_path, blank=True, null=True)
    invoice_name = models.CharField(max_length=255, blank=True, default="")
    invoice_size = models.BigIntegerField(default=0)
    status = models.CharField(max_length=20, choices=ExpenseStatus.choices, default=ExpenseStatus.PAYMENT_REQUESTED)
    approval_required = models.BooleanField(default=True)
    raised_by = models.ForeignKey(
        "employee.Employee", on_delete=models.CASCADE, related_name="vertex_expenses_raised"
    )
    approver = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True, default="")
    paid_by = models.ForeignKey(
        "employee.Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Expense")
        verbose_name_plural = _("Expenses")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} ({self.amount})"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("vertex-expense", args=[self.pk])

    def get_raiser_name(self):
        return self.raised_by.get_full_name()

    def get_amount_display(self):
        return f"₹{self.amount:,.2f}"

    def get_approval_display(self):
        return _("Needed") if self.approval_required else _("Not needed")

    def get_status_html(self):
        colour = {
            "payment_requested": "#3b82f6", "approved": "#8b5cf6", "rejected": "#dc2626", "paid": "#16a34a",
        }.get(self.status, "#6b7280")
        return _colour_html(colour, self.get_status_display())

    def get_invoice_url(self):
        from django.urls import reverse

        return reverse("vertex-expense-invoice", args=[self.pk])

    def get_download_url(self):
        from django.urls import reverse

        return reverse("vertex-expense-download", args=[self.pk])

    def get_approve_url(self):
        from django.urls import reverse

        return reverse("vertex-expense-approve", args=[self.pk])

    def get_reject_url(self):
        from django.urls import reverse

        return reverse("vertex-expense-reject", args=[self.pk])

    def get_pay_url(self):
        from django.urls import reverse

        return reverse("vertex-expense-pay", args=[self.pk])


class AllExpenses(Expense):
    """Permission switch only: ``view_allexpenses`` shows every expense."""

    class Meta:
        proxy = True
        verbose_name = _("Expenses (all)")
        verbose_name_plural = _("Expenses (all)")


class ExpenseApproval(Expense):
    """Permission switch only: ``change_expenseapproval`` may approve and reject expenses."""

    class Meta:
        proxy = True
        verbose_name = _("Expense approval")
        verbose_name_plural = _("Expense approval")


class ExpenseSetting(models.Model):
    """The one row of expense settings: approval rule and who gets the daily and weekly summaries."""

    approval_required_by_default = models.BooleanField(
        default=True,
        help_text=_("When on, every expense needs approval before it can be marked paid."),
    )
    daily_recipients = models.TextField(blank=True, default="", help_text=_("Comma separated emails"))
    weekly_recipients = models.TextField(blank=True, default="", help_text=_("Comma separated emails"))
    teams_webhook_url = models.URLField(max_length=500, blank=True, default="")

    class Meta:
        verbose_name = _("Expense setting")
        verbose_name_plural = _("Expense settings")

    def __str__(self):
        return "Expense settings"

    @classmethod
    def current(cls):
        setting, _created = cls.objects.get_or_create(pk=1)
        return setting


# ---------------------------------------------------------------------------
# Dashboard and talent analytics: permission switches only
# ---------------------------------------------------------------------------


class VertexDashboard(Project):
    """Permission switch only: ``view_vertexdashboard`` opens the Vertex overview page."""

    class Meta:
        proxy = True
        verbose_name = _("Vertex dashboard")
        verbose_name_plural = _("Vertex dashboard")


class TalentAnalytics(Appraisal):
    """Permission switch only: ``view_talentanalytics`` opens the analytics page (HR and leadership)."""

    class Meta:
        proxy = True
        verbose_name = _("Talent analytics")
        verbose_name_plural = _("Talent analytics")


class PayrollExport(Promotion):
    """Permission switch only: ``view_payrollexport`` downloads the CSV of completed promotions."""

    class Meta:
        proxy = True
        verbose_name = _("Payroll export")
        verbose_name_plural = _("Payroll export")
