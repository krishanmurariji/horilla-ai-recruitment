"""
vertex/tests/test_ui.py

The Vertex screens as a browser sees them: modal forms in the Vertex design, row action
menus on every list, the workspace tabs (also when swapped in by HTMX) and the small
display helpers behind them.
"""

from datetime import date, timedelta

from django.utils import timezone
from django.urls import reverse

from vertex.models import (
    MeetingMinute,
    Project,
    ProjectCall,
    ResourceRequest,
    ReviewRequest,
    StatusUpdate,
)
from vertex.tests.test_projects import (
    HX,
    VertexTestBase,
    grant,
    join,
    make_employee,
    make_project,
)

TABS = ("overview", "team", "documents", "calls", "status", "reviews", "comments")


class VertexUiTests(VertexTestBase):
    def setUp(self):
        super().setUp()
        self.admin = make_employee("Root", "root@example.com", "9300000001")
        user = self.admin.employee_user_id
        user.is_superuser = True
        user.save()
        self.dev = make_employee("Dev", "dev@example.com", "9300000002")
        self.project = make_project("UI1")
        join(self.project, self.dev)
        ProjectCall.objects.create(
            project=self.project, title="Kickoff", scheduled_at=timezone.now() + timedelta(days=2),
            duration_minutes=30,
        )
        StatusUpdate.objects.create(
            project=self.project, employee=self.dev, update_date=date.today(), work_completed="Did things",
        )
        # the Status Updates list shows the signed-in person's own updates
        StatusUpdate.objects.create(
            project=self.project, employee=self.admin, update_date=date.today(), work_completed="Admin did things",
        )
        ResourceRequest.objects.create(
            project=self.project, role_title="Tester", count=1, requested_by=self.dev,
        )
        ReviewRequest.objects.create(project=self.project, subject=self.dev, requested_by=self.admin)
        self.minute = MeetingMinute.objects.create(
            title="Sync", meeting_date=timezone.now(), created_by=self.admin, raw_notes="n",
        )
        self.client_admin = self.login(self.admin)

    # ---- modals ----------------------------------------------------------------------

    def modal_urls(self):
        return [
            reverse("vertex-project-new"),
            reverse("vertex-project-edit", args=[self.project.pk]),
            reverse("vertex-member-add", args=[self.project.pk]),
            reverse("vertex-document-add", args=[self.project.pk]),
            reverse("vertex-call-add", args=[self.project.pk]),
            reverse("vertex-status-new"),
            reverse("vertex-hiring-new"),
            reverse("vertex-review-new"),
            reverse("vertex-minute-new"),
            reverse("vertex-minute-edit", args=[self.minute.pk]),
        ]

    def test_every_modal_uses_the_vertex_design(self):
        for url in self.modal_urls():
            page = self.client_admin.get(url, **HX)
            self.assertEqual(page.status_code, 200, url)
            self.assertContains(page, "vm__head", msg_prefix=url)
            self.assertContains(page, "vm__foot", msg_prefix=url)
            self.assertContains(page, ">Cancel<", msg_prefix=url)

    def test_edit_modals_say_save_changes_and_hide_add_another(self):
        edit = self.client_admin.get(reverse("vertex-project-edit", args=[self.project.pk]), **HX)
        self.assertContains(edit, "Save changes")
        self.assertNotContains(edit, "Save and add another")
        new = self.client_admin.get(reverse("vertex-project-new"), **HX)
        self.assertContains(new, "Save and add another")
        # review and minutes create redirect afterwards, so no "add another"
        for name in ("vertex-review-new", "vertex-minute-new"):
            self.assertNotContains(self.client_admin.get(reverse(name), **HX), "Save and add another")

    def test_document_modal_switches_between_video_link_and_file(self):
        page = self.client_admin.get(reverse("vertex-document-add", args=[self.project.pk]), **HX)
        self.assertContains(page, 'id="vm-toggle-data"')
        self.assertContains(page, "video_url")
        # other modals have no toggle block
        other = self.client_admin.get(reverse("vertex-project-new"), **HX)
        self.assertNotContains(other, 'id="vm-toggle-data"')

    def test_locked_switch_is_not_greyed_like_a_text_field(self):
        page = self.client_admin.get(reverse("vertex-project-new"), **HX)
        self.assertContains(page, "input:disabled:not([type=checkbox])")
        self.assertContains(page, "input[type=checkbox]:disabled")

    def test_review_modal_has_the_meeting_section(self):
        page = self.client_admin.get(reverse("vertex-review-new"), **HX)
        self.assertContains(page, "Schedule a meeting (optional)")

    def test_an_invalid_modal_post_redisplays_the_vertex_form_with_errors(self):
        page = self.client_admin.post(reverse("vertex-project-new"), {"name": ""}, **HX)
        self.assertContains(page, "vm__head")
        self.assertContains(page, "errorlist")
        self.assertFalse(Project.objects.filter(name="").exists())

    def test_other_ariel_forms_are_untouched_by_the_vertex_modal_design(self):
        # the Vertex template is attached per form class, so a generic Ariel HR form keeps its own
        from base.forms import DepartmentForm

        self.assertIsNot(
            getattr(DepartmentForm, "structured", None).__module__ if hasattr(DepartmentForm, "structured") else None,
            "vertex.cbv",
        )

    # ---- lists ---------------------------------------------------------------------

    def test_every_list_has_a_row_action_menu(self):
        for name in (
            "vertex-project-list", "vertex-status-list", "vertex-hiring-list",
            "vertex-review-list", "vertex-minutes-list",
        ):
            page = self.client_admin.get(reverse(name), **HX)
            self.assertEqual(page.status_code, 200, name)
            self.assertContains(page, "row-action-menu", msg_prefix=name)
            self.assertContains(page, "Open", msg_prefix=name)

    def test_open_actions_are_not_cancelled_by_the_link_default(self):
        page = self.client_admin.get(reverse("vertex-review-list"), **HX)
        self.assertContains(page, "event.preventDefault();window.location.href")

    def test_minutes_delete_action_needs_the_delete_permission(self):
        owner = make_employee("Owner", "owner@example.com", "9300000003")
        grant(owner, "view_meetingminute", "change_meetingminute")
        MeetingMinute.objects.create(title="Mine", meeting_date=timezone.now(), created_by=owner, raw_notes="n")
        page = self.login(owner).get(reverse("vertex-minutes-list"), **HX)
        self.assertContains(page, "Edit")
        self.assertNotContains(page, "/delete/")

    def test_list_columns_have_proper_labels_and_dates(self):
        page = self.client_admin.get(reverse("vertex-minutes-list"), **HX)
        for label in ("Title", "Meeting date", "Location"):
            self.assertContains(page, label)
        self.assertNotContains(page, "+00:00")

    # ---- workspace -----------------------------------------------------------------

    def test_every_workspace_tab_renders_with_counts(self):
        for tab in TABS:
            page = self.client_admin.get(f"{self.project.get_absolute_url()}?tab={tab}")
            self.assertEqual(page.status_code, 200, tab)
            self.assertContains(page, 'id="vxTabs"', msg_prefix=tab)
        overview = self.client_admin.get(self.project.get_absolute_url())
        self.assertContains(overview, "Kickoff")  # next call
        self.assertContains(overview, "id things")  # latest status update (admin's or the developer's)

    def test_unknown_tab_falls_back_to_overview(self):
        page = self.client_admin.get(f"{self.project.get_absolute_url()}?tab=nonsense")
        self.assertContains(page, "About this project")

    def test_tab_links_swap_in_place_with_htmx(self):
        page = self.client_admin.get(self.project.get_absolute_url())
        self.assertContains(page, 'hx-select="#vxTabs"')
        self.assertContains(page, 'hx-push-url="true"')

    def test_timeline_shows_progress_and_overdue(self):
        project = make_project("LATE", expected_end_date=date.today() - timedelta(days=3))
        page = self.client_admin.get(project.get_absolute_url())
        self.assertContains(page, "days overdue")
        done = make_project("DONE", expected_end_date=date.today() - timedelta(days=3), status="completed")
        self.assertNotContains(self.client_admin.get(done.get_absolute_url()), "days overdue")

    def test_project_initials(self):
        self.assertEqual(Project(name="Mahesh S", code="X1").get_initials(), "MS")
        self.assertEqual(Project(name="alpha-beta gamma", code="X1").get_initials(), "AB")
        self.assertEqual(Project(name="  ", code="ab1").get_initials(), "AB")

    def test_a_member_without_manage_rights_sees_no_edit_button(self):
        grant(self.dev, "view_project")
        page = self.login(self.dev).get(self.project.get_absolute_url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Edit project")

    # ---- resource visibility and detail pages --------------------------------------------

    def test_resource_visibility_search_and_filter_form(self):
        page = self.client_admin.get(reverse("vertex-resources"), {"search": "Dev"})
        self.assertContains(page, "rv-toolbar")
        self.assertContains(page, "Dev")
        free = self.client_admin.get(reverse("vertex-resources"), {"availability": "Free"})
        self.assertEqual(free.status_code, 200)

    def test_detail_pages_render_in_the_shared_design(self):
        rr = ResourceRequest.objects.first()
        review = ReviewRequest.objects.first()
        for url in (
            reverse("vertex-resource-request", args=[rr.pk]),
            reverse("vertex-review", args=[review.pk]),
            reverse("vertex-minute", args=[self.minute.pk]),
        ):
            page = self.client_admin.get(url)
            self.assertEqual(page.status_code, 200, url)
            self.assertContains(page, "vx-card", msg_prefix=url)

    def test_breadcrumb_names_for_vertex_pages_are_readable(self):
        from horilla_crumbs.context_processors import BREADCRUMB_URL_NAMES, sidebar_urls

        for slug in ("vertex", "projects", "status-updates", "hiring", "resources", "reviews", "minutes"):
            self.assertIn(slug, BREADCRUMB_URL_NAMES)
        for slug in ("projects", "status-updates", "hiring", "resources", "reviews", "minutes"):
            self.assertIn(slug, sidebar_urls)


class VertexRegressionTests(VertexTestBase):
    """Bugs found while clicking through the finished screens."""

    def test_status_summary_counts_only_expected_people(self):
        from vertex import access
        from vertex.models import ProjectRole

        project = make_project("SUM")
        pm = make_employee("Pm", "pm@example.com", "9310000001")
        dev = make_employee("Dev", "dev2@example.com", "9310000002")
        join(project, pm, ProjectRole.PROJECT_MANAGER)
        join(project, dev, ProjectRole.DEVELOPER)
        StatusUpdate.objects.create(
            project=project, employee=pm, update_date=date.today(), work_completed="PM note"
        )
        summary = access.consolidated_summary(project, 7)
        # the PM reported, but only the developer is expected to, so nobody counts as reporting
        self.assertEqual((summary["reporting"], summary["expected"]), (0, 1))
        self.assertEqual(summary["missing"], [dev.get_full_name()])

    def test_optional_dates_start_empty_in_the_modals(self):
        admin = make_employee("Root", "root2@example.com", "9310000003")
        user = admin.employee_user_id
        user.is_superuser = True
        user.save()
        client = self.login(admin)
        project = make_project("DATES")
        for url, field in (
            (reverse("vertex-project-new"), "expected_end_date"),
            (reverse("vertex-member-add", args=[project.pk]), "end_date"),
            (reverse("vertex-review-new"), "due_date"),
        ):
            html = client.get(url, **HX).content.decode()
            tag = html[html.index(f'name="{field}"'):].split(">")[0]
            self.assertNotIn('value="', tag, field)
        # required dates keep today as their default
        html = client.get(reverse("vertex-project-new"), **HX).content.decode()
        tag = html[html.index('name="start_date"'):].split(">")[0]
        self.assertIn('value="', tag)

    def test_generated_minutes_render_as_formatted_html_and_stay_safe(self):
        from vertex.templatetags.vertex_extras import minutes_html

        html = minutes_html("# Title\n**Date:** now\n\n## Decisions\n- Ship <script>x</script>\n- Two")
        self.assertIn("<h3", html)
        self.assertIn("<b>Date:</b>", html)
        self.assertIn("<h4", html)
        self.assertEqual(html.count("<li>"), 2)
        self.assertNotIn("<script>", html)
        self.assertEqual(minutes_html(""), "")

    def test_tab_links_fall_back_to_a_full_page_load_when_the_swap_fails(self):
        admin = make_employee("Root", "root3@example.com", "9310000004")
        user = admin.employee_user_id
        user.is_superuser = True
        user.save()
        page = self.login(admin).get(make_project("TABS").get_absolute_url())
        self.assertContains(page, "hx-on::response-error")
        self.assertContains(page, "hx-on::send-error")

    def test_review_outcome_ratings_are_limited_in_the_form(self):
        from vertex.forms_reviews import CodeReviewForm, ProjectReviewForm

        html = str(ProjectReviewForm()["delivery_rating"])
        self.assertIn('min="1"', html)
        self.assertIn('max="5"', html)
        self.assertIn('max="5"', str(CodeReviewForm()["testing_rating"]))


class ToastTests(VertexTestBase):
    """A saved message is shown once: not twice from the reply, and not twice from the page."""

    def shown(self, response, text):
        """How many times a message text is drawn on the page."""
        return response.content.decode().count(text)

    def setUp(self):
        super().setUp()
        self.pm = make_employee("Pm", "pm@toast.example", "9500000001")
        grant(self.pm, "view_project", "add_project", "change_project", "view_reviewrequest", "view_goal")
        self.client_pm = self.login(self.pm)
        self.data = {"code": "TST1", "name": "Toast", "status": "active", "health": "green", "priority": 1,
                     "start_date": "2026-02-01"}

    def test_the_modal_reply_does_not_ask_for_the_toast_a_second_time(self):
        reply = self.client_pm.post(reverse("vertex-project-new"), self.data, **HX).content.decode()
        self.assertNotIn("reloadMessagesButton", reply)
        self.assertIn(".reload-record", reply)  # the list reload shows the toast

    def test_save_and_add_another_reopens_the_form_without_a_second_toast_request(self):
        reply = self.client_pm.post(
            reverse("vertex-project-new"), {**self.data, "save_and_add_another": "true"}, **HX
        ).content.decode()
        self.assertNotIn("reloadMessagesButton", reply)
        self.assertIn("htmx.ajax", reply)

    def test_a_full_page_shows_the_toast_once(self):
        self.client_pm.post(reverse("vertex-project-new"), self.data, **HX)
        project = Project.objects.get(code="TST1")
        self.data["name"] = "Toast 2"
        self.client_pm.post(
            reverse("vertex-project-edit", args=[project.pk]), self.data,
            HTTP_REFERER=f"http://testserver/vertex/projects/{project.pk}/", **HX,
        )
        page = self.client_pm.get(project.get_absolute_url())
        self.assertEqual(self.shown(page, "Project created."), 1)
        self.assertEqual(self.shown(page, "Project updated."), 1)

    def test_the_list_page_shows_the_toast_once(self):
        self.client_pm.post(reverse("vertex-project-new"), self.data, **HX)
        self.assertEqual(self.shown(self.client_pm.get(reverse("vertex-projects")), "Project created."), 1)
