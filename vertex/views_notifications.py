"""
vertex/views_notifications.py

The Vertex notification centre: the bell alerts that came from Vertex, in one page, with
"unread only", open (marks it read and goes to the item) and "mark all read". It reads the same
notifications as Ariel HR's bell, so reading one here clears it there too.
"""

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from notifications.models import Notification

from horilla.decorators import login_required
from horilla.methods import handle_no_permission
from horilla.post_only import post_only
from vertex import access
from vertex.notify_rules import RULES

PAGE_SIZE = 25


def vertex_notifications(user):
    """This person's notifications that point into Vertex (not deleted), newest first."""
    return Notification.objects.filter(
        recipient=user, deleted=False, data__redirect__startswith="/vertex/"
    ).order_by("-timestamp", "-pk")


def safe_link(link):
    """Only a path inside this site; anything else goes to the Vertex home."""
    link = (link or "").strip()
    if link.startswith("/") and not link.startswith("//") and "\\" not in link:
        return link
    return "/vertex/"


@login_required
def notification_centre(request):
    if not access.has_vertex_access(request.user):
        return handle_no_permission(request)
    unread_only = request.GET.get("filter") == "unread"
    items = vertex_notifications(request.user)
    unread_total = items.filter(unread=True).count()
    if unread_only:
        items = items.filter(unread=True)
    page = Paginator(items, PAGE_SIZE).get_page(request.GET.get("page"))
    rows = [
        {"id": n.pk, "text": n.verb, "unread": n.unread, "when": n.timestamp,
         "icon": (n.data or {}).get("icon", "notifications-outline")}
        for n in page
    ]
    return render(
        request,
        "vertex/notifications.html",
        {"rows": rows, "page": page, "unread_only": unread_only, "unread_total": unread_total, "rules": RULES},
    )


@login_required
@post_only
def notification_open(request, pk):
    if not access.has_vertex_access(request.user):
        return handle_no_permission(request)
    notification = vertex_notifications(request.user).filter(pk=pk).first()
    if notification is None:
        raise Http404
    if notification.unread:
        notification.mark_as_read()
    return redirect(safe_link((notification.data or {}).get("redirect")))


@login_required
@post_only
def notifications_read_all(request):
    if not access.has_vertex_access(request.user):
        return handle_no_permission(request)
    cleared = vertex_notifications(request.user).filter(unread=True).update(unread=False)
    messages.success(request, _("%(n)s notification(s) marked as read.") % {"n": cleared})
    return redirect("vertex-notifications")
