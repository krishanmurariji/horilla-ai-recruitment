"""
vertex/audit.py

Vertex on Ariel HR's audit trail. Ariel HR records who created, changed or deleted a record with
django-auditlog (the same log the Employee models use). Vertex models are registered here, always,
so the trail does not depend on the "tracked models" list an administrator edits in Settings.

* what is not tracked: the job claim rows (VertexSetting), which only hold "last run" dates, and the
  auto-maintained "updated at" stamps, which would make every save look like a change.
* the Teams webhook address of the expense settings is masked, because anyone who knows it can post
  to the channel.
* opening a payroll file or an invoice changes nothing, so it is recorded as an "access" entry.
"""

from django.apps import apps

# model name -> fields left out of the trail
TRACKED = {
    "Project": [],
    "ProjectMember": [],
    "ProjectDocument": [],
    "ProjectComment": [],
    "ProjectCall": [],
    "ReviewRequest": [],
    "ReviewMeeting": [],
    "ProjectReview": [],
    "CodeReview": [],
    "MeetingMinute": [],
    "StatusUpdate": [],
    "ResourceRequest": ["updated_at"],
    "ResourceRequestComment": [],
    "Feedback": ["updated_at"],
    "FeedbackCategoryScore": [],
    "PerformanceReport": ["updated_at"],
    "PerformanceCategoryScore": [],
    "Pip": [],
    "AppraisalCycle": [],
    "Appraisal": [],
    "Goal": [],
    "Promotion": [],
    "PromotionStep": [],
    "TrainingRecommendation": [],
    "Expense": [],
    "ExpenseSetting": [],
}
MASKED = {"ExpenseSetting": ["teams_webhook_url"]}


def mask_all(value):
    """Hide a secret completely (the log's own mask keeps the last characters, which is the secret part of a webhook)."""
    return "********"


def register_all():
    """Put every Vertex model on the audit trail (safe to call twice)."""
    from auditlog.registry import auditlog

    for name, excluded in TRACKED.items():
        model = apps.get_model("vertex", name)
        if auditlog.contains(model):
            continue
        fields = {f.name for f in model._meta.get_fields()}
        auditlog.register(
            model,
            exclude_fields=[f for f in excluded if f in fields],
            mask_fields=MASKED.get(name),
            mask_callable="vertex.audit.mask_all" if name in MASKED else None,
        )


def log_access(instance, what):
    """Record that someone opened or downloaded ``instance`` (the signed-in person is attached by Ariel's middleware)."""
    from auditlog.models import LogEntry

    return LogEntry.objects.log_create(
        instance, force_log=True, action=LogEntry.Action.ACCESS, changes={"access": ["", str(what)]},
    )
