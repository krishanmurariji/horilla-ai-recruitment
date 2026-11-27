"""
vertex/notify.py

In-app notifications (the bell) and e-mail for Vertex events. Both are best-effort:
a notification or mail problem never breaks the action that caused it.
"""

import logging

from django.utils.html import escape

from notifications.signals import notify

logger = logging.getLogger(__name__)


def notify_employees(sender, recipients, verb, redirect="", icon="people-outline"):
    """Bell notification to each employee (``sender`` is an Employee; None for an automatic job's reminder)."""
    sent = 0
    for employee in {e.pk: e for e in recipients if e is not None}.values():
        user = getattr(employee, "employee_user_id", None)
        if user is None or (sender is not None and employee == sender):
            continue
        try:
            # a notification needs someone it is "from": a job's reminder comes from the person it reminds
            extra = {} if sender is not None else {"label": "Ariel Vertex"}  # shown instead of "by <the person themselves>"
            notify.send(
                sender or employee, recipient=user, verb=verb, icon=icon, redirect=redirect or "/", **extra
            )
            sent += 1
        except Exception:
            logger.exception("Vertex notification failed for %s", employee)
    return sent


PARAGRAPH_STYLE = "margin:0 0 16px;font-size:14px;color:#444444;line-height:1.7;"


def text_to_html(text):
    """Plain text as letterhead paragraphs: a blank line starts a paragraph, a new line is a line break, and
    nothing typed can become markup."""
    normal = (text or "").replace(chr(13) + chr(10), chr(10))
    paragraphs = [p for p in normal.split(chr(10) * 2) if p.strip()]
    return "".join(
        f'<p style="{PARAGRAPH_STYLE}">{escape(paragraph.strip()).replace(chr(10), "<br>")}</p>' for paragraph in paragraphs
    )


MAIL_TEMPLATE = "vertex/mail/letterhead.html"


def build_mail(subject, body, email, tag="", team="Ariel Vertex", sender=None):
    """One Vertex mail on Ariel HR's company letterhead (the same one payslips and leave mails use): logo,
    watermark, a small tag at the top right and the team signature in the footer."""
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    from base.mail_letterhead import attach_letterhead_images, letterhead_context, resolve_letterhead_company

    company = resolve_letterhead_company()
    has_logo = bool(company and company.icon)
    context = letterhead_context(
        company, tag_label=tag, team_label=team, has_logo=has_logo, has_watermark=has_logo, content=text_to_html(body)
    )
    message = EmailMultiAlternatives(subject, body, sender, [email])
    message.attach_alternative(render_to_string(MAIL_TEMPLATE, context), "text/html")
    attached_logo, attached_watermark = attach_letterhead_images(message, company)
    if (attached_logo, attached_watermark) != (has_logo, has_logo):
        # the logo file could not be attached: draw the page without it rather than with a broken image
        context.update(has_logo=attached_logo, has_watermark=attached_watermark)
        message.alternatives = [(render_to_string(MAIL_TEMPLATE, context), "text/html")]
    return message


def send_plain_email(emails, subject, body, tag="", team="Ariel Vertex"):
    """Mail ``body`` to each address on the company letterhead; returns how many went out.
    ``tag`` is the small label at the top right."""
    from base.backends import ConfiguredEmailBackend

    sender = ConfiguredEmailBackend().dynamic_from_email_with_display_name
    sent = 0
    for email in emails:
        try:
            build_mail(subject, body, email, tag=tag, team=team, sender=sender).send()
            sent += 1
        except Exception:
            logger.exception("Vertex mail to %s failed", email)
    return sent


def employees_with_perm(perm):
    """Active employees who hold a permission, directly, through a group, or as admin."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.db.models import Q

    from employee.models import Employee

    app_label, codename = perm.split(".", 1)
    permission = Permission.objects.filter(
        content_type__app_label=app_label, codename=codename
    ).first()
    if permission is None:
        return Employee.objects.none()
    users = get_user_model().objects.filter(
        Q(is_superuser=True)
        | Q(groups__permissions=permission)
        | Q(user_permissions=permission)
    ).distinct()
    return Employee.objects.filter(employee_user_id__in=users, is_active=True)
