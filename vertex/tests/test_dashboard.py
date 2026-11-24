"""
vertex/tests/test_dashboard.py

The Vertex overview (role-aware home), the talent analytics page (completion, rating spread,
departments, promotions, training, the 9-box) and the payroll CSV of completed promotions.
"""

import csv
import io
from datetime import date
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from base.models import Department, JobPosition
from employee.models import EmployeeWorkInformation
from vertex import analytics, dashboard
from vertex.models import (
    Appraisal,
    AppraisalCycle,
    AppraisalStage,
    CycleStatus,
    Goal,
    GoalStatus,
    Promotion,
    PromotionStage,
    TrainingRecommendation,
    TrainingStatus,
)
from vertex.tests.test_projects import HX, VertexTestBase, grant, join, make_employee, make_project

DENIED = "You Dont Have Permission"


class DashboardBase(VertexTestBase):
    @classmethod
    def setUpTestData(cls):
        from horilla.horilla_middlewares import _thread_locals, set_selected_company

        _thread_locals.request = None
        set_selected_company(None)
        cls.eng = Department.objects.create(department="Engineering")
        cls.ops = Department.objects.create(department="Operations")
        cls.developer = JobPosition.objects.create(job_position="Developer", department_id=cls.eng)
        cls.senior = JobPosition.objects.create(job_position="Senior Developer", department_id=cls.eng)
        cls.hr = make_employee("Hema", "hr@d.example", "9810000001")
        cls.dev = make_employee("Dev", "dev@d.example", "9810000002")
        cls.ann = make_employee("Ann", "ann@d.example", "9810000003")
        cls.bob = make_employee("Bob", "bob@d.example", "9810000004")
        cls.boss = make_employee("Boss", "boss@d.example", "9810000005")
        cls.nobody = make_employee("Nobody", "nobody@d.example", "9810000006")
        for employee, department in ((cls.dev, cls.eng), (cls.ann, cls.eng), (cls.bob, cls.ops)):
            EmployeeWorkInformation.objects.update_or_create(
                employee_id=employee, defaults={"department_id": department, "job_position_id": cls.developer}
            )
        grant(cls.hr, "view_vertexdashboard", "view_project", "view_allprojects", "view_talentanalytics",
              "view_payrollexport", "view_promotion", "view_allpromotions")
        grant(cls.dev, "view_vertexdashboard", "view_project", "view_goal", "view_appraisal")
        grant(cls.boss, "view_vertexdashboard", "view_talentanalytics", "view_allpromotions", "view_promotion")
        grant(cls.ann, "view_vertexdashboard", "view_project")
        cls.cycle = AppraisalCycle.objects.create(
            name="H1", start_date=date(2027, 1, 1), end_date=date(2027, 6, 30), status=CycleStatus.ACTIVE
        )

    def appraise(self, employee, rating, stage=AppraisalStage.RELEASED, cycle=None):
        return Appraisal.objects.create(
            cycle=cycle or self.cycle, employee=employee, stage=stage, final_rating=rating
        )

    def goal(self, employee, progress, status=GoalStatus.IN_PROGRESS):
        return Goal.objects.create(
            employee=employee, title=f"G{progress}", weightage=10, target_date=date(2027, 3, 1),
            progress=progress, status=status,
        )


class OverviewTests(DashboardBase):
    def test_home_goes_to_the_overview_or_the_projects(self):
        self.assertRedirects(self.login(self.dev).get(reverse("vertex-home")), reverse("vertex-overview"))
        only_projects = make_employee("Only", "only@d.example", "9810000007")
        grant(only_projects, "view_project")
        self.assertRedirects(self.login(only_projects).get(reverse("vertex-home")), reverse("vertex-projects"))

    def test_no_permission_no_overview(self):
        response = self.login(self.nobody).get(reverse("vertex-overview"))
        self.assertEqual(response.status_code, 302)
        denied = self.login(self.nobody).get(reverse("vertex-overview"), **HX)
        self.assertContains(denied, DENIED)

    def test_cards_follow_the_modules_the_person_can_open(self):
        self.goal(self.dev, 40)
        self.goal(self.dev, 80)
        self.goal(self.dev, 10, status=GoalStatus.CANCELLED)
        self.appraise(self.dev, Decimal("4.0"), stage=AppraisalStage.SELF_PENDING)
        labels = [str(c["label"]) for c in dashboard.my_cards(self.dev.employee_user_id)]
        self.assertEqual(labels, ["My goals", "My appraisal"])
        goals = dashboard.my_cards(self.dev.employee_user_id)[0]
        self.assertEqual(goals["value"], "60%")  # cancelled goals are ignored
        self.assertEqual(dashboard.my_cards(self.ann.employee_user_id), [])

    def test_project_health_counts_only_projects_the_person_can_open(self):
        mine = make_project("DASH1", health="red")
        make_project("DASH2", health="green")
        join(mine, self.dev)
        page = dashboard.project_health(self.dev.employee_user_id)
        self.assertEqual(page["total"], 1)
        self.assertEqual({row["value"]: row["count"] for row in page["health"]}["red"], 1)
        all_of_them = dashboard.project_health(self.hr.employee_user_id)
        self.assertEqual(all_of_them["total"], 2)

    def test_upcoming_calls_are_scoped_and_in_the_future(self):
        from datetime import timedelta

        from vertex.models import ProjectCall

        mine, theirs = make_project("DASH3"), make_project("DASH4")
        join(mine, self.dev)
        soon = timezone.now() + timedelta(days=1)
        ProjectCall.objects.create(project=mine, title="Mine", scheduled_at=soon, duration_minutes=30)
        ProjectCall.objects.create(project=theirs, title="Theirs", scheduled_at=soon, duration_minutes=30)
        ProjectCall.objects.create(project=mine, title="Old", scheduled_at=timezone.now() - timedelta(days=2), duration_minutes=30)
        titles = [c.title for c in dashboard.upcoming_calls(self.dev.employee_user_id)]
        self.assertEqual(titles, ["Mine"])

    def test_pending_actions_only_list_what_waits_on_the_person(self):
        self.assertEqual(dashboard.pending_actions(self.dev.employee_user_id), [])
        self.appraise(self.dev, None, stage=AppraisalStage.SELF_PENDING)
        rows = dashboard.pending_actions(self.dev.employee_user_id)
        self.assertEqual([str(r["label"]) for r in rows], ["Self assessments to complete"])
        self.assertEqual(rows[0]["count"], 1)

    def test_the_page_renders_for_every_kind_of_person(self):
        self.appraise(self.dev, Decimal("4.0"))
        for employee in (self.hr, self.dev, self.ann, self.boss):
            page = self.login(employee).get(reverse("vertex-overview"))
            self.assertEqual(page.status_code, 200, employee)
            self.assertContains(page, "Overview")
        # the analytics link is only for people who can open it
        self.assertContains(self.login(self.hr).get(reverse("vertex-overview")), reverse("vertex-analytics"))
        self.assertNotContains(self.login(self.dev).get(reverse("vertex-overview")), reverse("vertex-analytics"))


class AnalyticsTests(DashboardBase):
    def test_completion_and_rating_buckets(self):
        self.appraise(self.dev, Decimal("4.7"))
        self.appraise(self.ann, Decimal("4.0"), stage=AppraisalStage.MANAGER_COMPLETED)
        self.appraise(self.bob, Decimal("2.5"), stage=AppraisalStage.SELF_SUBMITTED)
        self.assertEqual(analytics.completion(), {"total": 3, "completed": 2, "pending": 1, "rate": 67})
        counts = [row["count"] for row in analytics.rating_distribution()]
        self.assertEqual(counts, [1, 1, 0, 1])  # outstanding, excellent, good, needs improvement

    def test_an_empty_company_is_all_zeros(self):
        report = analytics.talent_report()
        self.assertEqual(report["completion"]["rate"], 0)
        self.assertEqual(report["departments"], [])
        self.assertEqual(report["nine_box"]["count"], 0)

    def test_department_average_uses_the_latest_rating_of_each_person(self):
        old = AppraisalCycle.objects.create(
            name="Old", start_date=date(2026, 1, 1), end_date=date(2026, 6, 30), status=CycleStatus.CLOSED
        )
        self.appraise(self.dev, Decimal("1.0"), cycle=old)  # superseded by the newer cycle
        self.appraise(self.dev, Decimal("4.0"))
        self.appraise(self.ann, Decimal("5.0"))
        self.appraise(self.bob, Decimal("3.0"))
        rows = {row["department"]: row for row in analytics.department_performance()}
        self.assertEqual(rows["Engineering"]["rating"], 4.5)
        self.assertEqual(rows["Engineering"]["count"], 2)
        self.assertEqual(rows["Operations"]["rating"], 3.0)

    def test_promotion_and_training_reports(self):
        Promotion.objects.create(employee=self.dev, proposed_position=self.senior, justification="x")
        Promotion.objects.create(
            employee=self.ann, proposed_position=self.senior, justification="x", stage=PromotionStage.COMPLETED
        )
        by_stage = {row["stage"]: row["count"] for row in analytics.promotion_report()}
        self.assertEqual(by_stage["Recommended"], 1)
        self.assertEqual(by_stage["Completed"], 1)
        for gap in ("SQL", "SQL", "Testing"):
            TrainingRecommendation.objects.create(employee=self.dev, skill_gap=gap, recommended_training="t")
        TrainingRecommendation.objects.create(
            employee=self.ann, skill_gap="SQL", recommended_training="t", status=TrainingStatus.COMPLETED
        )
        report = analytics.training_report()
        self.assertEqual(report["by_gap"][0], {"gap": "SQL", "count": 3})
        statuses = {row["status"]: row["count"] for row in report["by_status"]}
        self.assertEqual(statuses["Recommended"], 3)
        self.assertEqual(statuses["Completed"], 1)

    def test_nine_box_places_people_by_rating_and_goal_progress(self):
        self.appraise(self.dev, Decimal("4.5"))
        self.goal(self.dev, 90)
        self.appraise(self.ann, Decimal("2.0"))  # no goals count as 0% delivery
        self.appraise(self.bob, Decimal("3.5"))
        self.goal(self.bob, 50)
        self.goal(self.bob, 10, status=GoalStatus.CANCELLED)
        boxes = {p["name"]: p["box"] for p in analytics.nine_box()["people"]}
        self.assertEqual(boxes[self.dev.get_full_name()], "Star")
        self.assertEqual(boxes[self.ann.get_full_name()], "Risk")
        self.assertEqual(boxes[self.bob.get_full_name()], "Core player")
        grid = analytics.nine_box()["grid"]
        self.assertEqual(len(grid), 3)
        self.assertEqual(sum(len(c["people"]) for row in grid for c in row["cells"]), 3)

    def test_band_edges(self):
        self.assertEqual(analytics.box_label(Decimal("4"), 70), "Star")
        self.assertEqual(analytics.box_label(Decimal("3.9"), 69), "Core player")
        self.assertEqual(analytics.box_label(Decimal("3"), 40), "Core player")
        self.assertEqual(analytics.box_label(Decimal("2.9"), 39), "Risk")

    def test_page_access_follows_the_analytics_switch(self):
        url = reverse("vertex-analytics")
        for employee in (self.hr, self.boss):
            self.assertEqual(self.login(employee).get(url).status_code, 200, employee)
        for employee in (self.dev, self.nobody):
            self.assertEqual(self.login(employee).get(url).status_code, 302, employee)
            self.assertContains(self.login(employee).get(url, **HX), DENIED)

    def test_page_shows_the_numbers_and_the_export_button_only_with_the_switch(self):
        self.appraise(self.dev, Decimal("4.2"))
        self.goal(self.dev, 80)
        hr_page = self.login(self.hr).get(reverse("vertex-analytics"))
        self.assertContains(hr_page, "9-box talent matrix")
        self.assertContains(hr_page, self.dev.get_full_name())
        self.assertContains(hr_page, reverse("vertex-payroll-export"))
        boss_page = self.login(self.boss).get(reverse("vertex-analytics"))
        self.assertContains(boss_page, "9-box talent matrix")
        self.assertNotContains(boss_page, reverse("vertex-payroll-export"))


class PayrollExportTests(DashboardBase):
    def complete(self, employee, current, proposed, **extra):
        return Promotion.objects.create(
            employee=employee, proposed_position=self.senior, justification="x", stage=PromotionStage.COMPLETED,
            current_salary=current, proposed_salary=proposed, completed_at=timezone.now(), **extra,
        )

    def rows(self, response):
        return list(csv.reader(io.StringIO(response.content.decode())))

    def test_only_completed_promotions_with_the_computed_increment(self):
        self.complete(self.dev, 100000, 112500)
        self.complete(self.ann, None, 90000)  # no current salary: increment cannot be worked out
        Promotion.objects.create(employee=self.bob, proposed_position=self.senior, justification="x")
        response = self.login(self.hr).get(reverse("vertex-payroll-export"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        rows = self.rows(response)
        self.assertEqual(rows[0], analytics.PAYROLL_HEADER)
        self.assertEqual(len(rows), 3)
        by_name = {row[1]: row for row in rows[1:]}
        dev_row = by_name[self.dev.get_full_name()]
        self.assertEqual(dev_row[2], "Senior Developer")
        self.assertEqual(dev_row[5], "12.5")
        self.assertEqual(by_name[self.ann.get_full_name()][5], "")

    def test_increment_percent_rounding_and_empty_cases(self):
        self.assertEqual(analytics.increment_percent(Decimal("30000"), Decimal("33333")), Decimal("11.1"))
        self.assertIsNone(analytics.increment_percent(None, Decimal("1")))
        self.assertIsNone(analytics.increment_percent(Decimal("0"), Decimal("1")))
        self.assertEqual(analytics.increment_percent(Decimal("100"), Decimal("90")), Decimal("-10.0"))

    def test_formula_like_names_cannot_run_in_a_spreadsheet(self):
        self.dev.employee_first_name = "=HYPERLINK(1)"
        self.dev.save()
        self.complete(self.dev, 100, 110)
        rows = self.rows(self.login(self.hr).get(reverse("vertex-payroll-export")))
        self.assertTrue(rows[1][1].startswith("'="))

    def test_only_people_with_the_export_switch_can_download(self):
        url = reverse("vertex-payroll-export")
        self.assertEqual(self.login(self.hr).get(url).status_code, 200)
        for employee in (self.boss, self.dev, self.nobody):  # boss reads analytics but has no export switch
            self.assertEqual(self.login(employee).get(url).status_code, 302, employee)
