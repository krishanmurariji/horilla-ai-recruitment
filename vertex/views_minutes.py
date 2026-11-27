"""
vertex/views_minutes.py

Meeting minutes (ArielVertex MinutesController): capture notes -> generate -> preview ->
approve -> send to attendees. Only the person who captured the minutes can see or change
them (the same ownership rule as ArielVertex, which also applies to admins).
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from employee.models import Employee
from horilla.decorators import login_required, permission_required
from horilla.post_only import post_only
from vertex import access
from vertex.minutes import generate_minutes, split_emails
from vertex.models import MeetingMinute, MinuteStatus
from vertex.notify import notify_employees, send_plain_email


def _owned_or_404(request, pk):
    employee = access.employee_of(request.user)
    minute = MeetingMinute.objects.filter(pk=pk, created_by=employee).first() if employee else None
    if minute is None:
        raise Http404
    return minute


@login_required
@permission_required("vertex.view_meetingminute")
def minutes_detail(request, pk):
    minute = _owned_or_404(request, pk)
    user = request.user
    return render(
        request,
        "vertex/minutes_detail.html",
        {
            "minute": minute,
            "attendee_list": split_emails(minute.attendees),
            "can_change": user.has_perm("vertex.change_meetingminute"),
            "can_delete": user.has_perm("vertex.delete_meetingminute"),
        },
    )


@login_required
@permission_required("vertex.change_meetingminute")
@post_only
def minutes_generate(request, pk):
    minute = _owned_or_404(request, pk)
    if not minute.raw_notes.strip():
        messages.error(request, _("Add some meeting notes before generating minutes."))
    elif minute.status == MinuteStatus.SENT:
        messages.error(
            request,
            _("These minutes were already sent. Edit them to make corrections, then re-send."),
        )
    else:
        minute.minutes_text = generate_minutes(
            minute.title,
            timezone.localtime(minute.meeting_date),
            minute.location,
            split_emails(minute.attendees),
            minute.raw_notes,
        )
        minute.status = MinuteStatus.PREVIEW
        minute.save(update_fields=["minutes_text", "status"])
        messages.success(request, _("Minutes generated. Review the preview."))
    return redirect("vertex-minute", pk=minute.pk)


@login_required
@permission_required("vertex.change_meetingminute")
@post_only
def minutes_approve(request, pk):
    minute = _owned_or_404(request, pk)
    if not minute.minutes_text.strip():
        messages.error(request, _("Generate the minutes before approving."))
    else:
        minute.status = MinuteStatus.APPROVED
        minute.approved_by = access.employee_of(request.user)
        minute.approved_at = timezone.now()
        minute.save(update_fields=["status", "approved_by", "approved_at"])
        messages.success(request, _("Minutes approved."))
    return redirect("vertex-minute", pk=minute.pk)


@login_required
@permission_required("vertex.change_meetingminute")
@post_only
def minutes_send(request, pk):
    minute = _owned_or_404(request, pk)
    emails = split_emails(minute.attendees)
    if minute.status not in (MinuteStatus.APPROVED, MinuteStatus.SENT):
        messages.error(request, _("Approve the minutes before sending."))
    elif not minute.minutes_text.strip():
        messages.error(request, _("There are no minutes to send."))
    elif not emails:
        messages.error(request, _("Add at least one attendee to send the minutes to."))
    else:
        subject = f"Minutes of meeting — {minute.title}"
        body = f"Please find the minutes of '{minute.title}' below.\n\n{minute.minutes_text}"
        me = access.employee_of(request.user)
        internal = Employee.objects.filter(email__in=emails, is_active=True)
        notify_employees(me, internal, subject, "/vertex/minutes/")
        emailed = send_plain_email(emails, subject, body, tag=_("Meeting minutes"))
        minute.status = MinuteStatus.SENT
        minute.sent_at = timezone.now()
        minute.sent_count += 1
        minute.save(update_fields=["status", "sent_at", "sent_count"])
        messages.success(
            request,
            _("Minutes sent to %(n)d attendee(s); %(m)d e-mail(s) delivered.")
            % {"n": len(emails), "m": emailed},
        )
    return redirect("vertex-minute", pk=minute.pk)


@login_required
@permission_required("vertex.delete_meetingminute")
@post_only
def minutes_delete(request, pk):
    _owned_or_404(request, pk).delete()
    messages.success(request, _("Minutes deleted."))
    return redirect("vertex-minutes")
