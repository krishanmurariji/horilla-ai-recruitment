"""
vertex/tests/test_ops.py

The audit trail, the automatic jobs, the notification centre and the management / performance
print reports.
"""

import shutil
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest import mock

from auditlog.models import LogEntry
from auditlog.registry import auditlog
from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from notifications.models import Notification

from base.models import Department, JobPosition
from employee.models import EmployeeWorkInformation
from vertex import analytics, audit, jobs, notify_rules
from vertex.models import (
    Appraisal,
    AppraisalCycle,
    CycleStatus,
    Expense,
    ExpenseSetting,
    ExpenseStatus,
    Feedback,
    Goal,
    PerformancePeriodType,
    PerformanceReport,
    Project,
    ProjectRole,
    Promotion,
    PromotionStage,
    StatusUpdate,
)
from vertex.notify import notify_employees
from vertex.tests.test_projects import HX, VertexTestBase, grant, join, make_employee, make_project
from vertex.tests.test_hardening import make_admin

DENIED = "You Dont Have Permission"


def at(year, month, day, hour=10):
    return timezone.make_aware(datetime(year, month, day, hour, 0))


class OpsBase(VertexTestBase):
    media = None

    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls.media)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        shutil.rmtree(cls.media, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        from horilla.horilla_middlewares import _thread_locals, set_selected_company

        _thread_locals.request = None
        set_selected_company(None)
        cls.admin = make_admin("Root", "root@o.example", "9920000001")
        cls.hr = make_employee("Hema", "hr@o.example", "9920000002")
        cls.pm = make_employee("Priya", "pm@o.example", "9920000003")
        cls.dev = make_employee("Dev", "dev@o.example", "9920000004")
        cls.qa = make_employee("Quinn", "qa@o.example", "9920000005")
        cls.outsider = make_employee("Outsider", "out@o.example", "9920000006")
        cls.nobody = make_employee("Nobody", "nobody@o.example", "9920000007")
        grant(cls.hr, "view_vertexdashboard", "view_project", "view_allprojects", "view_managementreport",
              "view_resourcevisibility", "view_performancereport", "view_allperformancereports",
              "change_performancepublication", "add_performancereport")
        grant(cls.pm, "view_vertexdashboard", "view_project", "view_managementreport", "view_performancereport")
        grant(cls.dev, "view_vertexdashboard", "view_project", "view_myperformance")
        grant(cls.outsider, "view_vertexdashboard", "view_project", "view_managementreport")
        cls.project = Project.objects.create(code="OPS1", name="Ops one", start_date=date(2026, 1, 1))
        join(cls.project, cls.pm, ProjectRole.PROJECT_MANAGER)
        join(cls.project, cls.dev, ProjectRole.DEVELOPER)
        join(cls.project, cls.qa, ProjectRole.QA)
        cls.other = Project.objects.create(code="OPS2", name="Ops two", start_date=date(2026, 1, 1))
        join(cls.other, cls.outsider, ProjectRole.BUSINESS_PERSON)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditTests(OpsBase):
    def entries(self, model, **extra):
        return LogEntry.objects.filter(content_type__model=model.__name__.lower(), **extra)

    def test_every_vertex_model_is_on_the_trail_except_the_job_claims(self):
        models = {
            m.__name__ for m in apps.get_app_config("vertex").get_models() if not m._meta.proxy
        }
        self.assertEqual(models - set(audit.TRACKED), {"VertexSetting"})
        for name in audit.TRACKED:
            self.assertTrue(auditlog.contains(apps.get_model("vertex", name)), name)

    def test_a_record_made_changed_and_removed_through_the_pages_is_logged_with_who_did_it(self):
        client = self.login(self.admin)
        client.post(
            reverse("vertex-goal-new"),
            {"employee": self.dev.pk, "title": "Audit me", "category": "x", "weightage": "20", "target_date": "2027-06-30"},
            **HX,
        )
        goal = Goal.objects.get(title="Audit me")
        created = self.entries(Goal, object_pk=goal.pk, action=LogEntry.Action.CREATE).get()
        self.assertEqual(created.actor_id, self.admin.employee_user_id.pk)
        client.post(reverse("vertex-goal-edit", args=[goal.pk]), {
            "employee": self.dev.pk, "title": "Audit me too", "category": "x", "weightage": "20", "target_date": "2027-06-30",
        }, **HX)
        updated = self.entries(Goal, object_pk=goal.pk, action=LogEntry.Action.UPDATE).get()
        self.assertEqual(updated.changes_dict["title"], ["Audit me", "Audit me too"])
        self.assertEqual(updated.actor_id, self.admin.employee_user_id.pk)
        client.post(reverse("vertex-goal-delete", args=[goal.pk]))
        self.assertFalse(Goal.objects.filter(pk=goal.pk).exists())
        deleted = self.entries(Goal, object_pk=goal.pk, action=LogEntry.Action.DELETE).get()
        self.assertEqual(deleted.actor_id, self.admin.employee_user_id.pk)

    def test_an_expense_decision_and_payment_are_logged_against_the_person_who_made_them(self):
        acct = make_employee("Acct", "acct@o.example", "9920000010")
        desk = make_employee("Desk", "desk@o.example", "9920000011")
        grant(acct, "view_expense", "view_allexpenses", "change_expenseapproval", "change_expense")
        grant(desk, "view_expense", "add_expense", "change_expense")
        ExpenseSetting.objects.all().delete()
        self.login(desk).post(reverse("vertex-expense-new"), {
            "title": "Paper", "category": "office_supplies", "amount": "100", "expense_date": date.today().isoformat(),
            "payment_method": "cash", "approval_required": "on",
        }, **HX)
        expense = Expense.objects.get()
        self.assertEqual(self.entries(Expense, object_pk=expense.pk, action=LogEntry.Action.CREATE).get().actor_id, desk.employee_user_id.pk)
        self.login(acct).post(reverse("vertex-expense-approve", args=[expense.pk]), {"note": "ok"}, **HX)
        approval = self.entries(Expense, object_pk=expense.pk, action=LogEntry.Action.UPDATE).latest("pk")
        self.assertEqual(approval.changes_dict["status"], ["payment_requested", "approved"])
        self.assertEqual(approval.actor_id, acct.employee_user_id.pk)
        self.login(desk).post(reverse("vertex-expense-pay", args=[expense.pk]))
        payment = self.entries(Expense, object_pk=expense.pk, action=LogEntry.Action.UPDATE).latest("pk")
        self.assertEqual(payment.changes_dict["status"], ["approved", "paid"])
        self.assertEqual(payment.actor_id, desk.employee_user_id.pk)

    def test_the_teams_webhook_address_is_not_written_to_the_log(self):
        secret = "https://example.webhook.office.com/webhookb2/very-secret-token"
        setting = ExpenseSetting.current()
        setting.teams_webhook_url = secret
        setting.save()
        for entry in self.entries(ExpenseSetting):
            self.assertNotIn("very-secret-token", str(entry.changes))
            self.assertNotIn("very-secret-token", str(entry.serialized_data))

    def test_opening_an_invoice_or_the_payroll_file_is_recorded_as_access(self):
        desk = make_employee("Desk2", "desk2@o.example", "9920000012")
        grant(desk, "view_expense", "add_expense", "change_expense")
        ExpenseSetting.objects.all().delete()
        self.login(desk).post(reverse("vertex-expense-new"), {
            "title": "Bill", "category": "office_supplies", "amount": "10", "expense_date": date.today().isoformat(),
            "payment_method": "cash", "approval_required": "on",
            "invoice_file": SimpleUploadedFile("bill.pdf", b"%PDF-1.4\n%%EOF\n"),
        }, **HX)
        expense = Expense.objects.get()
        self.login(desk).get(reverse("vertex-expense-download", args=[expense.pk]))
        access = self.entries(Expense, object_pk=expense.pk, action=LogEntry.Action.ACCESS).get()
        self.assertEqual(access.actor_id, desk.employee_user_id.pk)
        self.assertIn("invoice", str(access.changes))
        grant(self.hr, "view_payrollexport")
        before = LogEntry.objects.filter(action=LogEntry.Action.ACCESS).count()
        self.login(self.hr).get(reverse("vertex-payroll-export"))
        self.assertEqual(LogEntry.objects.filter(action=LogEntry.Action.ACCESS).count(), before + 1)
        entry = LogEntry.objects.filter(action=LogEntry.Action.ACCESS).latest("pk")
        self.assertEqual(entry.actor_id, self.hr.employee_user_id.pk)
        self.assertIn("payroll CSV", str(entry.changes))

    def test_opening_a_cycle_logs_every_appraisal_it_creates(self):
        from vertex import appraisals

        cycle = AppraisalCycle.objects.create(
            name="Audit cycle", start_date=date(2027, 1, 1), end_date=date(2027, 6, 30), status=CycleStatus.DRAFT
        )
        created = appraisals.activate_cycle(cycle, self.hr)
        self.assertTrue(created)
        logged = self.entries(Appraisal, action=LogEntry.Action.CREATE).count()
        self.assertEqual(logged, Appraisal.objects.filter(cycle=cycle).count())

    def test_the_job_claim_rows_stay_out_of_the_log(self):
        from vertex.models import VertexSetting

        VertexSetting.objects.create(key="audit.test", value="x")
        self.assertFalse(self.entries(VertexSetting).exists())


# ---------------------------------------------------------------------------
# Automatic jobs
# ---------------------------------------------------------------------------


class JobsBase(OpsBase):
    def told(self, employee):
        return list(Notification.objects.filter(recipient=employee.employee_user_id).order_by("pk"))


class StatusReminderTests(JobsBase):
    MONDAY = (2027, 3, 1)

    def test_who_has_not_reported_is_reminded_and_the_manager_is_told_how_many(self):
        StatusUpdate.objects.create(project=self.project, employee=self.qa, update_date=date(2027, 3, 1), work_completed="done")
        result = jobs.status_reminders(date(2027, 3, 1))
        self.assertEqual(result, {"reminded": 1, "leads_told": 1})
        dev_note = self.told(self.dev)[0]
        self.assertIn("Ops one", dev_note.verb)
        self.assertEqual(dev_note.data["redirect"], reverse("vertex-status"))
        self.assertEqual(self.told(self.qa), [])  # already sent today's update
        self.assertIn("1 of your team", self.told(self.pm)[0].verb)

    def test_nobody_is_reminded_when_everyone_has_reported(self):
        for person in (self.dev, self.qa):
            StatusUpdate.objects.create(project=self.project, employee=person, update_date=date(2027, 3, 1), work_completed="done")
        self.assertEqual(jobs.status_reminders(date(2027, 3, 1)), {"reminded": 0, "leads_told": 0})

    def test_people_who_left_or_projects_that_are_not_active_are_left_alone(self):
        from vertex.models import ProjectMember, ProjectStatus

        ProjectMember.objects.filter(employee=self.dev).update(end_date=date(2027, 2, 1))
        ProjectMember.objects.filter(employee=self.qa).update(is_active=False)
        self.assertEqual(jobs.status_reminders(date(2027, 3, 1)), {"reminded": 0, "leads_told": 0})
        ProjectMember.objects.filter(employee=self.dev).update(end_date=None)
        Project.objects.filter(pk=self.project.pk).update(status=ProjectStatus.ON_HOLD)
        self.assertEqual(jobs.status_reminders(date(2027, 3, 1)), {"reminded": 0, "leads_told": 0})

    def test_it_runs_on_weekdays_after_four_once_a_day(self):
        self.assertEqual(jobs.run_due(at(2027, 3, 8, 15)), {})  # too early
        self.assertEqual(jobs.run_due(at(2027, 3, 13, 17)), {})  # a Saturday
        first = jobs.run_due(at(2027, 3, 8, 17))
        self.assertEqual(first["status_reminders"], {"reminded": 2, "leads_told": 1})
        self.assertNotIn("status_reminders", jobs.run_due(at(2027, 3, 8, 18)))  # same day: no second round
        self.assertIn("status_reminders", jobs.run_due(at(2027, 3, 9, 17)))  # next day: again
        self.assertEqual(len(self.told(self.dev)), 2)


class QuarterlyFeedbackTests(JobsBase):
    def test_the_project_manager_is_asked_once_for_each_person_without_feedback(self):
        Feedback.objects.create(subject=self.qa, author=self.pm, period="2027-Q1")
        result = jobs.quarterly_feedback_requests(date(2027, 3, 25))
        self.assertEqual(result["quarter"], "2027-Q1")
        self.assertEqual((result["reviewers_asked"], result["people"]), (1, 1))
        message = self.told(self.pm)[0].verb
        self.assertIn("Dev", message)
        self.assertNotIn("Quinn", message)  # feedback for the quarter already exists

    def test_the_reporting_manager_is_asked_when_the_project_has_no_manager(self):
        from vertex.models import ProjectMember

        ProjectMember.objects.filter(employee=self.pm).delete()
        EmployeeWorkInformation.objects.update_or_create(employee_id=self.dev, defaults={"reporting_manager_id": self.hr})
        jobs.quarterly_feedback_requests(date(2027, 3, 25))
        self.assertIn("Dev", self.told(self.hr)[0].verb)

    def test_only_in_the_last_week_of_a_quarter_and_once(self):
        self.assertEqual(jobs.run_due(at(2027, 3, 10)).get("quarterly_feedback"), None)
        self.assertEqual(jobs.run_due(at(2027, 4, 25)).get("quarterly_feedback"), None)  # April is not a quarter end
        self.assertIsNotNone(jobs.run_due(at(2027, 3, 25)).get("quarterly_feedback"))
        self.assertIsNone(jobs.run_due(at(2027, 3, 26)).get("quarterly_feedback"))
        self.assertIsNotNone(jobs.run_due(at(2027, 6, 25)).get("quarterly_feedback"))  # a new quarter, a new round


class MonthlyReportTests(JobsBase):
    def test_a_draft_report_of_last_month_for_each_developer_and_tester_and_hr_is_told(self):
        result = jobs.monthly_reports(date(2027, 2, 2))
        self.assertEqual(result, {"period": "2027-01", "generated": 2})
        reports = PerformanceReport.objects.filter(period="2027-01")
        self.assertEqual({r.subject_id for r in reports}, {self.dev.pk, self.qa.pk})
        self.assertFalse(any(r.is_published for r in reports))
        self.assertEqual(reports.first().period_type, PerformancePeriodType.MONTHLY)
        self.assertIn("2 draft performance report(s) for 2027-01", self.told(self.hr)[0].verb)

    def test_a_report_someone_already_reviewed_is_never_overwritten(self):
        from vertex import performance

        report = performance.generate_report(self.dev, PerformancePeriodType.MONTHLY, "2027-01")
        report.is_published = True
        report.strengths = "Edited by HR"
        report.save()
        self.assertEqual(jobs.monthly_reports(date(2027, 2, 2))["generated"], 1)  # only the tester
        report.refresh_from_db()
        self.assertTrue(report.is_published)
        self.assertEqual(report.strengths, "Edited by HR")

    def test_one_failing_report_does_not_stop_the_rest(self):
        from vertex import performance

        real = performance.generate_report

        def flaky(subject, period_type, period):
            if subject.pk == self.dev.pk:
                raise RuntimeError("boom")
            return real(subject, period_type, period)

        with mock.patch.object(performance, "generate_report", flaky):
            self.assertEqual(jobs.monthly_reports(date(2027, 2, 2))["generated"], 1)
        self.assertTrue(PerformanceReport.objects.filter(subject=self.qa, period="2027-01").exists())

    def test_runs_in_the_first_days_of_a_month_once(self):
        self.assertIsNone(jobs.run_due(at(2027, 2, 10)).get("monthly_reports"))
        self.assertIsNone(jobs.run_due(at(2027, 2, 2, 6)).get("monthly_reports"))  # before seven in the morning
        self.assertEqual(jobs.run_due(at(2027, 2, 2, 8))["monthly_reports"]["generated"], 2)
        self.assertIsNone(jobs.run_due(at(2027, 2, 3, 8)).get("monthly_reports"))
        self.assertEqual(jobs.previous_month(date(2027, 1, 2)), "2026-12")

    def test_a_job_that_crashes_is_logged_and_the_others_still_run(self):
        with mock.patch.object(jobs, "monthly_reports", side_effect=RuntimeError("boom")):
            ran = jobs.run_due(at(2027, 3, 1, 17))
        self.assertIn("status_reminders", ran)
        self.assertNotIn("monthly_reports", ran)


# ---------------------------------------------------------------------------
# Notification centre
# ---------------------------------------------------------------------------


class CommonNotificationTests(JobsBase):
    """Vertex alerts live in Ariel HR's own bell and inbox: one notification centre for everything."""

    def send(self, person, text, link="/vertex/expenses/"):
        notify_employees(self.admin, [person], text, link)
        return Notification.objects.filter(recipient=person.employee_user_id).latest("pk")

    def test_a_vertex_alert_appears_in_the_bell_and_opens_the_vertex_page(self):
        self.send(self.dev, "Your goal changed", "/vertex/goals/")
        bell = self.login(self.dev).get(reverse("notifications"))
        self.assertContains(bell, "Your goal changed")
        self.assertContains(bell, 'href="/vertex/goals/#"')
        everything = self.login(self.dev).get(reverse("all-notifications"))
        self.assertContains(everything, "Your goal changed")

    def test_an_automatic_reminder_is_from_ariel_vertex_not_from_the_person_it_is_for(self):
        jobs.status_reminders(date(2027, 3, 1))
        note = Notification.objects.filter(recipient=self.dev.employee_user_id).latest("pk")
        self.assertEqual(note.data["label"], "Ariel Vertex")
        for url in ("notifications", "all-notifications"):
            page = self.login(self.dev).get(reverse(url))
            self.assertContains(page, "You have not sent today", msg_prefix=url)
            self.assertContains(page, "Ariel Vertex", msg_prefix=url)
            flat = " ".join(page.content.decode().split())
            self.assertNotIn(f"ago by {self.dev.get_full_name()}", flat, url)

    def test_an_alert_caused_by_a_person_still_names_that_person(self):
        self.send(self.dev, "A human did this")
        page = self.login(self.dev).get(reverse("notifications"))
        self.assertContains(page, self.admin.get_full_name())
        self.assertNotContains(page, "Ariel Vertex")

    def test_opening_it_from_the_bell_marks_it_read_for_everything(self):
        note = self.send(self.dev, "Mark me")
        from notifications.utils import id2slug

        self.login(self.dev).get(reverse("notifications:mark_as_read", args=[id2slug(note.pk)]))
        note.refresh_from_db()
        self.assertFalse(note.unread)

    def test_the_separate_vertex_notification_page_is_gone(self):
        from django.urls import NoReverseMatch

        for name in ("vertex-notifications", "vertex-notifications-read-all"):
            with self.assertRaises(NoReverseMatch):
                reverse(name)
        self.assertEqual(self.login(self.dev).get("/vertex/notifications/").status_code, 404)

    def test_the_overview_lists_which_events_send_a_notification(self):
        page = self.login(self.dev).get(reverse("vertex-overview"))
        self.assertContains(page, "Which Vertex events send you a notification")
        self.assertContains(page, str(notify_rules.RULES[0][1]))
        self.assertContains(page, "at the top right")


# ---------------------------------------------------------------------------
# Management and performance print reports
# ---------------------------------------------------------------------------


class ReportTests(OpsBase):
    def test_each_person_sees_only_the_projects_they_can_open(self):
        mine = self.login(self.pm).get(reverse("vertex-reports"))
        self.assertEqual([r["project"].code for r in mine.context["projects"]], ["OPS1"])
        self.assertEqual(mine.context["kpis"]["assignments"], 3)
        self.assertIsNone(mine.context["buckets"])  # no resource visibility switch
        everyone = self.login(self.hr).get(reverse("vertex-reports"))
        self.assertEqual([r["project"].code for r in everyone.context["projects"]], ["OPS1", "OPS2"])
        self.assertEqual(everyone.context["kpis"]["projects"], 2)
        self.assertTrue(everyone.context["buckets"])

    def test_the_reports_need_their_own_switch(self):
        for name in ("vertex-reports", "vertex-reports-print", "vertex-reports-csv"):
            self.assertEqual(self.login(self.dev).get(reverse(name)).status_code, 302, name)
            self.assertContains(self.login(self.dev).get(reverse(name), **HX), DENIED, msg_prefix=name)

    def test_the_csv_lists_the_visible_projects_and_stops_formulas(self):
        Project.objects.filter(pk=self.other.pk).update(name="=HYPERLINK(1)", client_name="+cmd")
        import csv, io

        grant(self.outsider, "view_allprojects")
        response = self.login(self.outsider).get(reverse("vertex-reports-csv"))
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0], ["Code", "Name", "Status", "Health", "Priority", "Client", "Members"])
        self.assertEqual({r[0] for r in rows[1:]}, {"OPS1", "OPS2"})
        two = next(r for r in rows if r[0] == "OPS2")
        self.assertTrue(two[1].startswith("'=") and two[5].startswith("'+"))
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        scoped = list(csv.reader(io.StringIO(self.login(self.pm).get(reverse("vertex-reports-csv")).content.decode())))
        self.assertEqual([r[0] for r in scoped[1:]], ["OPS1"])

    def test_the_print_page_is_standalone_and_escapes_what_was_typed(self):
        Project.objects.filter(pk=self.project.pk).update(name="<script>alert(1)</script>")
        page = self.login(self.pm).get(reverse("vertex-reports-print"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Save as PDF")
        self.assertContains(page, "Management Report")
        self.assertNotContains(page, "<script>alert(1)")
        self.assertNotContains(page, "oh-sidebar")  # no application chrome: it is a print page

    def test_a_performance_report_prints_for_those_who_may_see_it_only(self):
        from vertex import performance

        report = performance.generate_report(self.dev, PerformancePeriodType.MONTHLY, "2027-01")
        url = reverse("vertex-performance-report-print", args=[report.pk])
        page = self.login(self.hr).get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Performance Report")
        self.assertContains(page, self.dev.get_full_name())
        self.assertContains(page, "Draft (not yet published)")
        self.assertContains(page, "Save as PDF")
        self.assertEqual(self.login(self.outsider).get(url).status_code, 302)  # no report permission
        grant(self.outsider, "view_performancereport")
        self.assertEqual(self.login(self.outsider).get(url).status_code, 404)  # a report that is not theirs to see
        report.is_published = True
        report.published_at = timezone.now()
        report.save()
        self.assertContains(self.login(self.hr).get(url), "Published")

    def test_the_report_page_links_to_the_print_version(self):
        from vertex import performance

        report = performance.generate_report(self.dev, PerformancePeriodType.MONTHLY, "2027-01")
        page = self.login(self.hr).get(report.get_absolute_url())
        self.assertContains(page, reverse("vertex-performance-report-print", args=[report.pk]))
