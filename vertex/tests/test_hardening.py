"""
vertex/tests/test_hardening.py

Hostile and awkward use, not the happy path: script text in every field, a stranger opening
other people's records, GET and token-less POST on actions, repeated and stale actions, odd
numbers and dates, bad uploads, and deleting a person who has Vertex records.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from base.models import Department, JobPosition
from employee.models import EmployeeWorkInformation
from vertex import expenses, promotions
from vertex.models import (
    Appraisal,
    AppraisalCycle,
    AppraisalStage,
    CycleStatus,
    Expense,
    ExpenseStatus,
    Feedback,
    Goal,
    MeetingMinute,
    Project,
    ProjectComment,
    ProjectDocument,
    ProjectMember,
    Promotion,
    PromotionStage,
    ResourceRequest,
    StatusUpdate,
    TrainingRecommendation,
)
from vertex.tests.test_expenses import ExpenseBase
from vertex.tests.test_projects import HX, PASSWORD, grant, join, make_employee

PAYLOAD = '<script>alert(1)</script>"><img src=x onerror=alert(1)>'
RAW_MARKS = ("<script>alert(1)", "<img src=x onerror")
MARK = "SECRET-MARK-7731"


def make_admin(first, email, phone):
    employee = make_employee(first, email, phone)
    user = employee.employee_user_id
    user.is_superuser = True
    user.save()
    return employee


class HardeningBase(ExpenseBase):
    """The expense people (desk, acct, hr, boss, nobody) plus an admin, a developer and one of each record."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = make_admin("Root", "root@h.example", "9910000001")
        cls.dev = make_employee("Dev", "dev@h.example", "9910000002")
        cls.stranger = make_employee("Stranger", "stranger@h.example", "9910000003")
        dept = Department.objects.create(department="Engineering")
        cls.position = JobPosition.objects.create(job_position="Developer", department_id=dept)
        cls.senior = JobPosition.objects.create(job_position="Senior Developer", department_id=dept)
        EmployeeWorkInformation.objects.update_or_create(
            employee_id=cls.dev, defaults={"department_id": dept, "job_position_id": cls.position}
        )
        grant(cls.dev, "view_project", "view_statusupdate", "add_statusupdate", "view_goal", "view_appraisal")
        # a stranger can open every module but is in nobody's team and sees nobody's records
        grant(
            cls.stranger, "view_project", "view_statusupdate", "view_resourcerequest", "view_reviewrequest",
            "view_meetingminute", "view_feedback", "view_performancereport", "view_pip", "view_myperformance",
            "view_appraisal", "view_goal", "view_promotion", "view_trainingrecommendation", "view_expense",
            "view_vertexdashboard",
        )

    def records(self, text):
        """One of every kind of record with ``text`` in its free-text fields."""
        project = Project.objects.create(code="HARD1", name=text[:200], description=text, start_date=date(2026, 1, 1))
        join(project, self.dev)
        ProjectComment.objects.create(project=project, author=self.dev, message=text)
        StatusUpdate.objects.create(
            project=project, employee=self.dev, update_date=date.today(), work_completed=text, blockers=text
        )
        request = ResourceRequest.objects.create(
            project=project, requested_by=self.dev, role_title=text, skills=text, reason=text, count=1
        )
        minute = MeetingMinute.objects.create(
            title=text, meeting_date=timezone.now(), created_by=self.admin, raw_notes=text, minutes_text=text
        )
        cycle = AppraisalCycle.objects.create(
            name=f"Cycle {text}"[:150], start_date=date(2027, 1, 1), end_date=date(2027, 6, 30), status=CycleStatus.ACTIVE
        )
        appraisal = Appraisal.objects.create(
            cycle=cycle, employee=self.dev, stage=AppraisalStage.SELF_SUBMITTED, self_comments=text,
            self_rating=Decimal("3.0"), manager_comments=text,
        )
        goal = Goal.objects.create(
            employee=self.dev, title=text, description=text, weightage=10, target_date=date(2027, 3, 1)
        )
        training = TrainingRecommendation.objects.create(
            employee=self.dev, skill_gap=text[:200], recommended_training=text
        )
        promotion = Promotion.objects.create(
            employee=self.dev, recommended_by=self.admin, proposed_position=self.senior, justification=text,
            current_designation=text[:100],
        )
        expense = Expense.objects.create(
            title=text[:120], description=text, vendor=text[:120], amount=Decimal("10"), expense_date=date.today(),
            raised_by=self.desk,
        )
        feedback = Feedback.objects.create(
            subject=self.dev, author=self.admin, period="2027-Q1", constructive_summary=text, strengths=text,
            improvement_areas=text, internal_notes=text,
        )
        return dict(
            project=project, request=request, minute=minute, cycle=cycle, appraisal=appraisal, goal=goal,
            training=training, promotion=promotion, expense=expense, feedback=feedback,
        )

    def pages(self, r):
        """Every readable page and modal that can show the records, as URLs."""
        names = [
            "vertex-overview", "vertex-analytics", "vertex-projects", "vertex-project-list", "vertex-project-card",
            "vertex-project-nav", "vertex-status", "vertex-status-list", "vertex-hiring", "vertex-hiring-list",
            "vertex-resources", "vertex-reviews", "vertex-review-list", "vertex-minutes", "vertex-minutes-list",
            "vertex-feedback", "vertex-feedback-list", "vertex-performance", "vertex-performance-list",
            "vertex-pip", "vertex-pip-list", "vertex-cycles", "vertex-cycle-list", "vertex-appraisals",
            "vertex-appraisal-list", "vertex-goals", "vertex-goal-list", "vertex-promotions",
            "vertex-promotion-list", "vertex-training", "vertex-training-list", "vertex-expenses",
            "vertex-expense-list", "vertex-expense-settings", "vertex-my-performance",
        ]
        urls = [reverse(name) for name in names]
        for tab in ("overview", "team", "documents", "calls", "status", "reviews", "comments"):
            urls.append(f"{r['project'].get_absolute_url()}?tab={tab}")
        urls += [
            reverse("vertex-project-edit", args=[r["project"].pk]),
            reverse("vertex-resource-request", args=[r["request"].pk]),
            reverse("vertex-minute", args=[r["minute"].pk]),
            reverse("vertex-minute-edit", args=[r["minute"].pk]),
            reverse("vertex-appraisal", args=[r["appraisal"].pk]),
            reverse("vertex-appraisal-manager", args=[r["appraisal"].pk]),
            reverse("vertex-cycle-edit", args=[r["cycle"].pk]),
            reverse("vertex-goal-edit", args=[r["goal"].pk]),
            reverse("vertex-goal-progress", args=[r["goal"].pk]),
            reverse("vertex-training-edit", args=[r["training"].pk]),
            reverse("vertex-promotion", args=[r["promotion"].pk]),
            reverse("vertex-promotion-validate", args=[r["promotion"].pk]),
            reverse("vertex-expense", args=[r["expense"].pk]),
            reverse("vertex-expense-approve", args=[r["expense"].pk]),
            reverse("vertex-expense-invoice", args=[r["expense"].pk]),
            reverse("vertex-feedback-detail", args=[r["feedback"].pk]),
            reverse("vertex-feedback-edit", args=[r["feedback"].pk]),
        ]
        return urls


class HostileTextTests(HardeningBase):
    def test_script_text_is_escaped_on_every_page(self):
        records = self.records(PAYLOAD)
        # Ariel refuses script text in a name when it is saved, so write it straight to the table
        type(self.dev).objects.filter(pk=self.dev.pk).update(employee_first_name=PAYLOAD)
        client = self.login(self.admin)
        offenders = []
        for url in self.pages(records):
            for extra in ({}, HX):
                page = client.get(url, **extra)
                self.assertLess(page.status_code, 500, url)
                body = page.content.decode()
                if any(mark in body for mark in RAW_MARKS):
                    offenders.append(url)
        self.assertEqual(offenders, [], "script text reached the page unescaped")


class StrangerTests(HardeningBase):
    def test_a_person_outside_the_team_never_sees_other_peoples_records(self):
        records = self.records(MARK)
        client = self.login(self.stranger)
        for url in self.pages(records):
            for extra in ({}, HX):
                page = client.get(url, **extra)
                self.assertLess(page.status_code, 500, url)
                self.assertNotIn(MARK, page.content.decode(), f"{url} {extra}")

    def test_a_stranger_cannot_act_on_other_peoples_records(self):
        records = self.records("plain")
        client = self.login(self.stranger)
        expense, goal, promotion = records["expense"], records["goal"], records["promotion"]
        client.post(reverse("vertex-expense-pay", args=[expense.pk]))
        client.post(reverse("vertex-goal-delete", args=[goal.pk]))
        client.post(reverse("vertex-goal-progress", args=[goal.pk]), {"progress": "99", "status": "completed"}, **HX)
        client.post(reverse("vertex-promotion-validate", args=[promotion.pk]), {"note": "x"}, **HX)
        client.post(reverse("vertex-promotion-reject", args=[promotion.pk]), {"note": "x"}, **HX)
        client.post(reverse("vertex-expense-approve", args=[expense.pk]), {"note": "x"}, **HX)
        client.post(
            reverse("vertex-appraisal-self", args=[records["appraisal"].pk]),
            {"self_rating": "5", "self_comments": "x"}, **HX,
        )
        for obj in (expense, goal, promotion, records["appraisal"]):
            obj.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAYMENT_REQUESTED)
        self.assertEqual((goal.progress, Goal.objects.count()), (0, 1))
        self.assertEqual(promotion.stage, PromotionStage.RECOMMENDED)
        self.assertEqual(records["appraisal"].self_rating, Decimal("3.0"))


class RequestMethodTests(HardeningBase):
    def actions(self, r):
        """(url, still-unchanged check) for every action that changes or removes something."""
        return [
            (reverse("vertex-expense-pay", args=[r["expense"].pk]), lambda: Expense.objects.get(pk=r["expense"].pk).status == ExpenseStatus.PAYMENT_REQUESTED),
            (reverse("vertex-goal-delete", args=[r["goal"].pk]), lambda: Goal.objects.filter(pk=r["goal"].pk).exists()),
            (reverse("vertex-training-delete", args=[r["training"].pk]), lambda: TrainingRecommendation.objects.filter(pk=r["training"].pk).exists()),
            (reverse("vertex-minute-delete", args=[r["minute"].pk]), lambda: MeetingMinute.objects.filter(pk=r["minute"].pk).exists()),
            (reverse("vertex-cycle-close", args=[r["cycle"].pk]), lambda: AppraisalCycle.objects.get(pk=r["cycle"].pk).status == CycleStatus.ACTIVE),
            (reverse("vertex-feedback-approve", args=[r["feedback"].pk]), lambda: Feedback.objects.get(pk=r["feedback"].pk).status == "draft"),
        ]

    def test_opening_an_action_link_changes_nothing(self):
        records = self.records("plain")
        client = self.login(self.admin)
        for url, unchanged in self.actions(records):
            for extra in ({}, HX):
                client.get(url, **extra)
                self.assertTrue(unchanged(), f"GET {url} {extra}")

    def test_a_post_without_the_csrf_token_is_refused(self):
        records = self.records("plain")
        client = Client(enforce_csrf_checks=True)
        self.assertTrue(client.login(username=self.admin.employee_user_id.username, password=PASSWORD))
        for url, unchanged in self.actions(records):
            response = client.post(url, {})
            self.assertEqual(response.status_code, 403, url)
            self.assertTrue(unchanged(), url)


class RepeatedActionTests(HardeningBase):
    def test_an_expense_decided_or_paid_twice_changes_once(self):
        records = self.records("plain")
        expense = records["expense"]
        expenses.approve(expense.pk, self.acct, "ok")
        with self.assertRaises(expenses.ExpenseError):
            expenses.approve(expense.pk, self.acct, "again")
        with self.assertRaises(expenses.ExpenseError):
            expenses.reject(expense.pk, self.acct, "too late")
        expenses.pay(expense.pk, self.desk)
        with self.assertRaises(expenses.ExpenseError):
            expenses.pay(expense.pk, self.desk)
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.PAID)

    def test_a_promotion_cannot_skip_a_stage_or_repeat_one(self):
        promotion = self.records("plain")["promotion"]
        with self.assertRaises(promotions.PromotionError):
            promotions.complete(promotion.pk, self.admin)  # not approved yet
        promotions.validate(promotion.pk, self.admin)
        with self.assertRaises(promotions.PromotionError):
            promotions.validate(promotion.pk, self.admin)
        promotion.refresh_from_db()
        self.assertEqual(promotion.stage, PromotionStage.HR_VALIDATED)
        self.assertEqual(promotion.steps.count(), 1)

    def test_a_manager_form_opened_before_the_release_cannot_undo_the_release(self):
        appraisal = self.records("plain")["appraisal"]
        appraisal.stage = AppraisalStage.RELEASED
        appraisal.final_rating = Decimal("4.0")
        appraisal.save()
        data = {"manager_rating": "2", "manager_comments": "late edit"}
        self.login(self.admin).post(reverse("vertex-appraisal-manager", args=[appraisal.pk]), data, **HX)
        appraisal.refresh_from_db()
        self.assertEqual(appraisal.stage, AppraisalStage.RELEASED)
        self.assertEqual(appraisal.final_rating, Decimal("4.0"))


class AwkwardInputTests(HardeningBase):
    def raise_expense(self, **extra):
        client = self.login(self.desk)
        before = Expense.objects.count()
        response = client.post(reverse("vertex-expense-new"), self.data(**extra), **HX)
        self.assertLess(response.status_code, 500)
        return Expense.objects.count() - before, response

    def test_bad_amounts_are_form_errors(self):
        for amount in ("0", "-5", "abc", "", "1e999", "NaN", "Infinity", "99999999999999999999", "1,5", "0.001"):
            created, response = self.raise_expense(amount=amount)
            self.assertEqual(created, 0, amount)
            self.assertEqual(response.status_code, 200, amount)

    def test_bad_dates_text_and_choices_are_form_errors(self):
        for field, value in (
            ("expense_date", "2027-02-30"), ("expense_date", "not a date"), ("category", "nonsense"),
            ("payment_method", "barter"), ("title", ""), ("title", "x" * 5000), ("title", "bad\x00title"),
            ("vendor", "bad\x00vendor"), ("description", "y" * 100000),
        ):
            created, _response = self.raise_expense(**{field: value})
            self.assertEqual(created, 0, f"{field}={value[:20]!r}")

    def test_unicode_and_emoji_are_kept_whole(self):
        created, _response = self.raise_expense(title="Café ☕ पेपर 😀", vendor="दुकान")
        self.assertEqual(created, 1)
        self.assertEqual(Expense.objects.get().title, "Café ☕ पेपर 😀")

    def test_goal_progress_outside_zero_to_hundred_is_refused(self):
        goal = self.records("plain")["goal"]
        client = self.login(self.dev)
        for value in ("101", "-1", "abc", "1e3", "50.5", ""):
            client.post(reverse("vertex-goal-progress", args=[goal.pk]), {"progress": value, "status": "in_progress"}, **HX)
            goal.refresh_from_db()
            self.assertEqual(goal.progress, 0, value)

    def test_odd_goal_weights_and_dates_are_refused(self):
        records = self.records("plain")
        records["goal"].delete()
        client = self.login(self.admin)
        base = {"employee": self.dev.pk, "title": "G", "category": "c", "target_date": "2027-06-30"}
        for weight in ("0", "101", "-3", "abc", "1e2"):
            client.post(reverse("vertex-goal-new"), {**base, "weightage": weight}, **HX)
        client.post(reverse("vertex-goal-new"), {**base, "weightage": "10", "target_date": "2027-13-45"}, **HX)
        client.post(reverse("vertex-goal-new"), {**base, "weightage": "10", "title": "bad\x00"}, **HX)
        self.assertEqual(Goal.objects.count(), 0)

    def test_nul_bytes_in_a_status_update_or_comment_do_not_crash_the_page(self):
        project = self.records("plain")["project"]
        client = self.login(self.dev)
        client.post(
            reverse("vertex-comment-add", args=[project.pk]), {"message": "hi\x00there"}
        )
        client.post(
            reverse("vertex-status-new"),
            {"project": project.pk, "update_date": date.today().isoformat(), "work_completed": "did\x00x"}, **HX,
        )
        self.assertEqual(client.get(project.get_absolute_url()).status_code, 200)


class UploadTests(HardeningBase):
    def upload(self, name, content, extra_data=None):
        client = self.login(self.desk)
        before = Expense.objects.count()
        data = self.data(**(extra_data or {}))
        data["invoice_file"] = SimpleUploadedFile(name, content)
        response = client.post(reverse("vertex-expense-new"), data, **HX)
        self.assertLess(response.status_code, 500, name)
        return Expense.objects.count() - before

    def test_dangerous_or_disguised_files_are_refused(self):
        pdf = b"%PDF-1.4\n%%EOF\n"
        cases = {
            "run.exe": b"MZ\x90\x00", "page.html": b"<script>alert(1)</script>", "image.svg": b"<svg onload=alert(1)>",
            "invoice.pdf.exe": pdf, "invoice.php.jpg": b"<?php echo 1;", "fake.pdf": b"<html><script>x</script></html>",
            "empty.pdf": b"", "noext": pdf, ".pdf": b"<html>", "a.pdf\x00.exe": pdf,
        }
        for name, content in cases.items():
            self.assertEqual(self.upload(name, content), 0, name)

    def test_a_path_in_the_file_name_never_reaches_the_disk(self):
        self.assertEqual(self.upload("../../evil.pdf", b"%PDF-1.4\n%%EOF\n"), 1)
        stored = Expense.objects.get().invoice.name
        self.assertNotIn("..", stored)
        self.assertRegex(stored, r"^vertex/expense/invoice/[0-9a-f]{32}\.pdf$")

    def test_the_download_is_an_attachment_the_browser_cannot_run(self):
        self.upload("bill.pdf", b"%PDF-1.4\n%%EOF\n")
        expense = Expense.objects.get()
        response = self.login(self.desk).get(reverse("vertex-expense-download", args=[expense.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.login(self.stranger).get(reverse("vertex-expense-download", args=[expense.pk])).status_code, 404)


class DeletingAPersonTests(HardeningBase):
    def test_company_records_outlive_the_person_who_made_them(self):
        records = self.records("plain")
        expense, feedback, minute = records["expense"], records["feedback"], records["minute"]
        expense_title = expense.title
        # the people who raised the expense, wrote the feedback and created the minutes leave the company
        # (Ariel refuses to delete someone with a contract, so remove the contracts first)
        from payroll.models.models import Contract

        Contract.objects.filter(employee_id__in=[self.desk.pk, self.admin.pk]).delete()
        self.desk.delete()
        self.admin.delete()
        self.assertTrue(Expense.objects.filter(pk=expense.pk, title=expense_title).exists(), "expense deleted with its raiser")
        self.assertTrue(Feedback.objects.filter(pk=feedback.pk).exists(), "feedback deleted with its author")
        self.assertTrue(MeetingMinute.objects.filter(pk=minute.pk).exists(), "minutes deleted with their creator")
        # and the pages that show them still open
        client = self.login(self.acct)
        grant(self.acct, "view_allexpenses")
        self.assertEqual(client.get(reverse("vertex-expense", args=[expense.pk])).status_code, 200)
        self.assertEqual(client.get(reverse("vertex-expense-list"), **HX).status_code, 200)

    def test_an_expense_can_still_be_decided_after_its_raiser_has_left(self):
        from payroll.models.models import Contract

        expense = self.records("plain")["expense"]
        Contract.objects.filter(employee_id=self.desk.pk).delete()
        self.desk.delete()
        expenses.approve(expense.pk, self.acct, "ok")
        expense.refresh_from_db()
        self.assertEqual(expense.status, ExpenseStatus.APPROVED)
        self.assertIsNone(expense.raised_by)
        self.assertEqual(str(expense.get_raiser_name()), "Former employee")

    def test_deleting_a_project_member_or_project_leaves_no_orphans(self):
        project = self.records("plain")["project"]
        project.delete()
        self.assertEqual(ProjectMember.objects.count(), 0)
        self.assertEqual(ProjectComment.objects.count(), 0)
        self.assertEqual(StatusUpdate.objects.count(), 0)
        self.assertEqual(ProjectDocument.objects.count(), 0)
