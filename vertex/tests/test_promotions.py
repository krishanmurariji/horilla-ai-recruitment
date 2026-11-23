"""
vertex/tests/test_promotions.py

Promotions (recommend -> HR validates -> leadership approves -> HR completes, or rejected) and
Learning (training and the suggestion rules), ported from ArielVertex with the gaps fixed.
"""

import json
from datetime import date
from decimal import Decimal

from django.urls import reverse
from notifications.models import Notification

from base.models import Department, JobPosition
from employee.models import EmployeeWorkInformation
from vertex import access, learning, promotions
from vertex.models import (
    Goal,
    GoalStatus,
    PerformanceCategoryScore,
    PerformanceReport,
    Promotion,
    PromotionStage,
    TrainingRecommendation,
    TrainingSource,
    TrainingStatus,
    VertexSetting,
)
from vertex.tests.test_projects import HX, VertexTestBase, grant, make_employee

DENIED = "You Dont Have Permission"


def report_to(employee, manager, position=None):
    EmployeeWorkInformation.objects.update_or_create(
        employee_id=employee, defaults={"reporting_manager_id": manager, "job_position_id": position}
    )


class PromotionBase(VertexTestBase):
    @classmethod
    def setUpTestData(cls):
        from horilla.horilla_middlewares import _thread_locals, set_selected_company

        _thread_locals.request = None
        set_selected_company(None)
        dept = Department.objects.create(department="Engineering")
        cls.developer = JobPosition.objects.create(job_position="Developer", department_id=dept)
        cls.senior = JobPosition.objects.create(job_position="Senior Developer", department_id=dept)
        cls.hr = make_employee("Hema", "hr@p.example", "9800000001")
        cls.manager = make_employee("Manu", "mgr@p.example", "9800000002")
        cls.dev = make_employee("Dev", "dev@p.example", "9800000003")
        cls.other = make_employee("Other", "other@p.example", "9800000004")
        cls.boss = make_employee("Boss", "boss@p.example", "9800000005")
        cls.viewer = make_employee("View", "view@p.example", "9800000006")
        report_to(cls.dev, cls.manager, cls.developer)
        report_to(cls.manager, cls.hr, cls.developer)
        grant(cls.hr, "view_promotion", "add_promotion", "view_allpromotions", "change_promotionvalidation",
              "view_trainingrecommendation", "add_trainingrecommendation", "change_trainingrecommendation",
              "delete_trainingrecommendation", "view_alltraining")
        grant(cls.manager, "view_promotion", "add_promotion", "view_trainingrecommendation",
              "add_trainingrecommendation", "change_trainingrecommendation", "delete_trainingrecommendation")
        grant(cls.dev, "view_trainingrecommendation", "view_myperformance")
        grant(cls.other, "view_promotion", "view_trainingrecommendation")
        grant(cls.boss, "view_promotion", "view_allpromotions", "change_promotionapproval")
        grant(cls.viewer, "view_promotion", "view_allpromotions")

    def data(self, **extra):
        data = {
            "employee": self.dev.pk, "proposed_position": self.senior.pk, "proposed_salary": "120000",
            "justification": "Consistently delivers and mentors others.",
        }
        data.update(extra)
        return data

    def recommend(self, who=None, **extra):
        self.login(who or self.manager).post(reverse("vertex-promotion-new"), self.data(**extra), **HX)

    def make(self, stage=PromotionStage.RECOMMENDED, employee=None, **extra):
        return Promotion.objects.create(
            employee=employee or self.dev, recommended_by=self.manager, current_designation="Developer",
            proposed_position=self.senior, justification="Good", stage=stage, **extra,
        )

    def step(self, who, name, promotion, note=""):
        return self.login(who).post(reverse(f"vertex-promotion-{name}", args=[promotion.pk]), {"note": note}, **HX)

    def count(self, person):
        return Notification.objects.filter(recipient=person.employee_user_id).count()


class RecommendTests(PromotionBase):
    def test_a_manager_recommends_a_report_and_hr_is_told_but_the_employee_is_not(self):
        self.recommend()
        promotion = Promotion.objects.get()
        self.assertEqual((promotion.stage, promotion.recommended_by, promotion.current_designation),
                         (PromotionStage.RECOMMENDED, self.manager, "Developer"))
        self.assertEqual(list(promotion.steps.values_list("to_stage", flat=True)), ["recommended"])
        self.assertEqual(self.count(self.hr), 1)
        self.assertEqual(self.count(self.dev), 0)

    def test_a_manager_can_only_recommend_their_own_reports_but_hr_anyone(self):
        self.recommend(employee=self.other.pk)
        self.assertFalse(Promotion.objects.exists())
        self.recommend(who=self.hr, employee=self.other.pk)
        self.assertEqual(Promotion.objects.get().employee, self.other)

    def test_a_developer_cannot_recommend(self):
        self.assertContains(self.login(self.dev).get(reverse("vertex-promotion-new"), **HX), DENIED)
        self.recommend(who=self.dev)
        self.assertFalse(Promotion.objects.exists())

    def test_one_open_promotion_per_person_and_a_new_one_after_a_rejection(self):
        self.recommend()
        self.recommend()
        self.assertEqual(Promotion.objects.count(), 1)
        promotions.reject(Promotion.objects.get().pk, self.hr, "Not this year")
        self.recommend()
        self.assertEqual(Promotion.objects.count(), 2)

    def test_the_database_itself_refuses_a_second_open_promotion(self):
        from django.db import IntegrityError, transaction

        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make()

    def test_the_same_designation_salary_and_text_are_checked(self):
        self.recommend(proposed_position=self.developer.pk)
        self.recommend(proposed_salary="0")
        self.recommend(justification="   ")
        self.recommend(justification="j" * 1001)
        self.assertFalse(Promotion.objects.exists())
        page = self.login(self.manager).get(reverse("vertex-promotion-new"), **HX)
        self.assertContains(page, 'maxlength="1000"')
        self.recommend(justification="j" * 1000, proposed_salary="")
        self.assertEqual(Promotion.objects.get().proposed_salary, None)


class WorkflowTests(PromotionBase):
    def test_the_full_flow_changes_the_designation_and_tells_the_employee_only_at_the_end(self):
        self.recommend()
        promotion = Promotion.objects.get()
        self.step(self.hr, "validate", promotion, "Looks right")
        promotion.refresh_from_db()
        self.assertEqual(promotion.stage, PromotionStage.HR_VALIDATED)
        self.step(self.boss, "approve", promotion, "Agreed")
        promotion.refresh_from_db()
        self.assertEqual(promotion.stage, PromotionStage.APPROVED)
        self.assertEqual(self.count(self.dev), 0)
        self.assertEqual(self.dev.get_job_position(), self.developer)
        self.step(self.hr, "complete", promotion)
        promotion.refresh_from_db()
        self.assertEqual(promotion.stage, PromotionStage.COMPLETED)
        self.assertIsNotNone(promotion.completed_at)
        self.dev.refresh_from_db()
        self.assertEqual(EmployeeWorkInformation.objects.get(employee_id=self.dev).job_position_id, self.senior)
        self.assertEqual(self.count(self.dev), 1)
        self.assertEqual(
            list(promotion.steps.values_list("to_stage", flat=True)),
            ["recommended", "hr_validated", "approved", "completed"],
        )
        self.assertEqual(promotion.steps.get(to_stage="hr_validated").note, "Looks right")

    def test_leadership_is_told_when_hr_validates_and_hr_when_leadership_approves(self):
        grant(self.boss, "view_promotion")  # already has it; makes the intent explicit
        promotion = self.make()
        before = self.count(self.boss)
        promotions.validate(promotion.pk, self.hr)
        self.assertEqual(self.count(self.boss), before + 1)
        before = self.count(self.hr)
        promotions.approve(promotion.pk, self.boss)
        self.assertEqual(self.count(self.hr), before + 1)

    def test_every_step_needs_the_right_permission_and_the_right_stage(self):
        promotion = self.make()
        self.assertContains(self.login(self.boss).get(reverse("vertex-promotion-validate", args=[promotion.pk]), **HX), DENIED)
        self.assertContains(self.login(self.hr).get(reverse("vertex-promotion-approve", args=[promotion.pk]), **HX), DENIED)
        self.assertContains(self.login(self.hr).get(reverse("vertex-promotion-complete", args=[promotion.pk]), **HX), DENIED)
        self.assertContains(self.login(self.manager).get(reverse("vertex-promotion-validate", args=[promotion.pk]), **HX), DENIED)
        self.step(self.boss, "validate", promotion)
        self.step(self.hr, "complete", promotion)
        promotion.refresh_from_db()
        self.assertEqual(promotion.stage, PromotionStage.RECOMMENDED)

    def test_a_step_cannot_happen_twice(self):
        promotion = self.make()
        promotions.validate(promotion.pk, self.hr)
        with self.assertRaises(promotions.PromotionError):
            promotions.validate(promotion.pk, self.hr)
        self.assertEqual(promotion.steps.filter(to_stage="hr_validated").count(), 1)

    def test_nobody_decides_their_own_promotion(self):
        promotion = self.make(employee=self.hr)
        self.assertContains(self.login(self.hr).get(reverse("vertex-promotion-validate", args=[promotion.pk]), **HX), DENIED)
        self.assertContains(self.login(self.hr).get(reverse("vertex-promotion-reject", args=[promotion.pk]), **HX), DENIED)

    def test_a_rejection_needs_a_reason_and_is_not_told_to_the_employee(self):
        promotion = self.make()
        self.step(self.hr, "reject", promotion, "   ")
        promotion.refresh_from_db()
        self.assertTrue(promotion.is_open)
        before = self.count(self.manager)
        self.step(self.hr, "reject", promotion, "Budget is frozen")
        promotion.refresh_from_db()
        self.assertEqual((promotion.stage, promotion.decision_note), (PromotionStage.REJECTED, "Budget is frozen"))
        self.assertEqual(self.count(self.manager), before + 1)
        self.assertEqual(self.count(self.dev), 0)
        self.assertEqual(promotion.steps.last().note, "Budget is frozen")

    def test_leadership_and_hr_can_reject_at_any_open_stage_but_not_after_completion(self):
        for stage, who in ((PromotionStage.RECOMMENDED, self.hr), (PromotionStage.HR_VALIDATED, self.boss),
                           (PromotionStage.APPROVED, self.hr)):
            promotion = self.make(stage=stage)
            self.step(who, "reject", promotion, "No")
            promotion.refresh_from_db()
            self.assertEqual(promotion.stage, PromotionStage.REJECTED, stage)
        done = self.make(stage=PromotionStage.COMPLETED)
        self.assertContains(self.login(self.hr).get(reverse("vertex-promotion-reject", args=[done.pk]), **HX), DENIED)

    def test_completing_needs_work_information_and_changes_nothing_without_it(self):
        nobody = make_employee("NoWork", "nowork@p.example", "9800000007")
        EmployeeWorkInformation.objects.filter(employee_id=nobody).delete()  # every employee starts with an empty one
        promotion = self.make(stage=PromotionStage.APPROVED, employee=nobody)
        with self.assertRaises(promotions.PromotionError):
            promotions.complete(promotion.pk, self.hr)
        promotion.refresh_from_db()
        self.assertEqual(promotion.stage, PromotionStage.APPROVED)

    def test_the_salary_is_never_changed_and_hr_is_pointed_to_payroll(self):
        promotion = self.make(stage=PromotionStage.APPROVED, proposed_salary=Decimal("150000"))
        promotions.complete(promotion.pk, self.hr)
        page = self.login(self.hr).get(promotion.get_absolute_url())
        self.assertContains(page, "Pay Revisions")
        self.assertNotContains(self.login(self.viewer).get(promotion.get_absolute_url()), "Pay Revisions")


class VisibilityTests(PromotionBase):
    def test_who_sees_which_promotions(self):
        mine = self.make()
        elsewhere = self.make(employee=self.manager)  # recommended by the manager, about the manager
        ids = lambda who: set(access.visible_promotions(who.employee_user_id).values_list("pk", flat=True))
        self.assertEqual(ids(self.hr), {mine.pk, elsewhere.pk})
        self.assertEqual(ids(self.boss), ids(self.hr))
        self.assertEqual(ids(self.manager), {mine.pk, elsewhere.pk})  # recommended by them
        self.assertEqual(ids(self.other), set())
        self.assertEqual(ids(self.dev), set())  # no view_promotion at all

    def test_the_employee_sees_only_a_completed_promotion_about_themselves(self):
        grant(self.dev, "view_promotion")
        pending = self.make()
        ids = lambda: set(access.visible_promotions(self.dev.employee_user_id).values_list("pk", flat=True))
        self.assertEqual(ids(), set())
        self.assertEqual(self.login(self.dev).get(pending.get_absolute_url()).status_code, 404)
        promotions.validate(pending.pk, self.hr)
        promotions.approve(pending.pk, self.boss)
        promotions.complete(pending.pk, self.hr)
        self.assertEqual(ids(), {pending.pk})
        rejected = self.make(employee=self.manager, stage=PromotionStage.REJECTED)
        self.assertNotIn(rejected.pk, ids())

    def test_salary_shows_to_hr_leadership_and_the_recommender_only(self):
        promotion = Promotion.objects.create(
            employee=self.dev, recommended_by=self.hr, current_designation="Developer",
            proposed_position=self.senior, justification="Good", proposed_salary=Decimal("98765"),
        )
        shows = lambda who: "98765" in self.login(who).get(promotion.get_absolute_url()).content.decode()
        self.assertTrue(shows(self.hr))
        self.assertTrue(shows(self.boss))
        self.assertTrue(shows(self.viewer))
        # the reporting manager sees the request but not the figure, since HR recommended it
        self.assertEqual(self.login(self.manager).get(promotion.get_absolute_url()).status_code, 200)
        self.assertFalse(shows(self.manager))
        mine = self.make(employee=self.manager, proposed_salary=Decimal("43210"))
        self.assertIn("43210", self.login(self.manager).get(mine.get_absolute_url()).content.decode())

    def test_row_actions_follow_the_stage_and_the_person(self):
        promotion = self.make()
        listing = lambda who: self.login(who).get(reverse("vertex-promotion-list"), **HX).content.decode()
        self.assertIn("/validate/", listing(self.hr))
        self.assertNotIn("/validate/", listing(self.manager))
        self.assertNotIn("/approve/", listing(self.boss))
        promotions.validate(promotion.pk, self.hr)
        self.assertIn("/approve/", listing(self.boss))
        self.assertNotIn("/approve/", listing(self.hr))
        self.assertIn("/reject/", listing(self.boss))


class PagesTests(PromotionBase):
    def test_admin_opens_every_page(self):
        admin = make_employee("Admin", "admin@p.example", "9800000008")
        user = admin.employee_user_id
        user.is_superuser = True
        user.save()
        promotion = self.make()
        training = TrainingRecommendation.objects.create(employee=self.dev, skill_gap="g", recommended_training="t")
        client = self.login(admin)
        for url in (reverse("vertex-promotions"), reverse("vertex-training"), promotion.get_absolute_url()):
            self.assertEqual(client.get(url).status_code, 200, url)
        for url in (
            reverse("vertex-promotion-nav"), reverse("vertex-promotion-list"), reverse("vertex-promotion-new"),
            reverse("vertex-promotion-validate", args=[promotion.pk]), reverse("vertex-promotion-reject", args=[promotion.pk]),
            reverse("vertex-training-nav"), reverse("vertex-training-list"), reverse("vertex-training-new"),
            reverse("vertex-training-suggest"), reverse("vertex-training-edit", args=[training.pk]),
            reverse("vertex-training-status", args=[training.pk]),
        ):
            page = client.get(url, **HX)
            self.assertEqual(page.status_code, 200, url)
            self.assertNotContains(page, DENIED, msg_prefix=url)

    def test_suggest_training_button_sits_in_the_nav_next_to_assign(self):
        admin = make_employee("Admin2", "admin2@p.example", "9800000018")
        user = admin.employee_user_id
        user.is_superuser = True
        user.save()
        client = self.login(admin)
        nav = client.get(reverse("vertex-training-nav"), **HX)
        self.assertContains(nav, "Suggest training")
        self.assertContains(nav, "Assign training")
        self.assertNotContains(client.get(reverse("vertex-training")), "Suggest training")

    def test_my_performance_shows_a_completed_promotion_and_my_training(self):
        promotion = self.make()
        TrainingRecommendation.objects.create(employee=self.dev, skill_gap="Gap one", recommended_training="Course one")
        page = self.login(self.dev).get(reverse("vertex-my-performance"))
        self.assertContains(page, "Course one")
        self.assertNotContains(page, "Senior Developer")
        promotions.validate(promotion.pk, self.hr)
        promotions.approve(promotion.pk, self.boss)
        promotions.complete(promotion.pk, self.hr)
        self.assertContains(self.login(self.dev).get(reverse("vertex-my-performance")), "Senior Developer")

    def test_no_permission_no_pages(self):
        nobody = make_employee("Nobody", "nobody@p.example", "9800000009")
        client = self.login(nobody)
        for name in ("vertex-promotions", "vertex-training"):
            self.assertNotEqual(client.get(reverse(name)).status_code, 200, name)


class TrainingTests(PromotionBase):
    def data_t(self, **extra):
        data = {"employee": self.dev.pk, "skill_gap": "Estimation", "recommended_training": "Estimation workshop", "duration_months": 2}
        data.update(extra)
        return data

    def test_a_manager_assigns_training_to_a_report_and_the_report_is_told(self):
        self.login(self.manager).post(reverse("vertex-training-new"), self.data_t(), **HX)
        training = TrainingRecommendation.objects.get()
        self.assertEqual((training.source, training.status, training.created_by), (TrainingSource.MANUAL, TrainingStatus.RECOMMENDED, self.manager))
        self.assertEqual(self.count(self.dev), 1)

    def test_scope_limits_and_text_checks(self):
        client = self.login(self.manager)
        client.post(reverse("vertex-training-new"), self.data_t(employee=self.other.pk), **HX)
        for bad in (dict(duration_months=0), dict(duration_months=25), dict(skill_gap="  "),
                    dict(recommended_training=""), dict(skill_gap="s" * 201), dict(recommended_training="t" * 201)):
            client.post(reverse("vertex-training-new"), self.data_t(**bad), **HX)
        self.assertFalse(TrainingRecommendation.objects.exists())
        self.login(self.hr).post(reverse("vertex-training-new"), self.data_t(employee=self.other.pk), **HX)
        self.assertEqual(TrainingRecommendation.objects.get().employee, self.other)

    def test_status_is_updated_by_the_owner_or_the_manager_only(self):
        training = TrainingRecommendation.objects.create(employee=self.dev, skill_gap="g", recommended_training="t")
        url = reverse("vertex-training-status", args=[training.pk])
        self.login(self.dev).post(url, {"status": "in_progress"}, **HX)
        training.refresh_from_db()
        self.assertEqual(training.status, TrainingStatus.IN_PROGRESS)
        self.assertContains(self.login(self.other).get(url, **HX), DENIED)
        self.login(self.manager).post(url, {"status": "completed"}, **HX)
        training.refresh_from_db()
        self.assertEqual(training.status, TrainingStatus.COMPLETED)

    def test_edit_and_delete_belong_to_the_manager_and_hr(self):
        training = TrainingRecommendation.objects.create(employee=self.dev, skill_gap="g", recommended_training="t")
        self.assertContains(self.login(self.dev).get(reverse("vertex-training-edit", args=[training.pk]), **HX), DENIED)
        self.login(self.dev).post(training.get_delete_url())
        self.login(self.other).post(training.get_delete_url())
        self.assertTrue(TrainingRecommendation.objects.exists())
        self.login(self.manager).post(training.get_delete_url())
        self.assertFalse(TrainingRecommendation.objects.exists())

    def test_training_visibility(self):
        mine = TrainingRecommendation.objects.create(employee=self.dev, skill_gap="g", recommended_training="t")
        theirs = TrainingRecommendation.objects.create(employee=self.other, skill_gap="g", recommended_training="t")
        ids = lambda who: set(access.visible_training(who.employee_user_id).values_list("pk", flat=True))
        self.assertEqual(ids(self.dev), {mine.pk})
        self.assertEqual(ids(self.manager), {mine.pk})
        self.assertEqual(ids(self.hr), {mine.pk, theirs.pk})
        self.assertEqual(ids(self.other), {theirs.pk})


class SuggestionTests(PromotionBase):
    def goal(self, category, progress, status=GoalStatus.IN_PROGRESS, **extra):
        return Goal.objects.create(employee=self.dev, title=f"{category} goal", category=category, progress=progress,
                                   weightage=10, target_date=date(2027, 1, 1), status=status, **extra)

    def test_weak_goal_categories_become_mapped_training(self):
        self.goal("Project Delivery", 20)
        self.goal("Project Delivery", 40)
        self.goal("Leadership", 90)
        created = learning.suggest(self.dev, self.manager)
        self.assertEqual(len(created), 1)
        training = created[0]
        self.assertEqual((training.recommended_training, training.duration_months, training.source),
                         ("Agile Delivery & Estimation Workshop", 2, TrainingSource.SUGGESTED))
        self.assertIn("Project Delivery", training.skill_gap)
        self.assertEqual(self.count(self.dev), 1)

    def test_nothing_is_invented_when_there_is_no_gap(self):
        self.goal("Leadership", 90)
        self.assertEqual(learning.suggest(self.dev, self.manager), [])
        self.assertFalse(TrainingRecommendation.objects.exists())
        self.assertEqual(learning.suggest(self.other, self.manager), [])  # no goals at all

    def test_cancelled_and_uncategorised_goals_are_ignored(self):
        self.goal("Leadership", 0, status=GoalStatus.CANCELLED)
        self.goal("", 0)
        self.assertEqual(learning.suggest(self.dev, self.manager), [])

    def test_a_gap_with_open_training_is_skipped_but_a_finished_one_comes_back(self):
        self.goal("Collaboration", 10)
        first = learning.suggest(self.dev, self.manager)
        self.assertEqual(len(first), 1)
        self.assertEqual(learning.suggest(self.dev, self.manager), [])
        first[0].status = TrainingStatus.COMPLETED
        first[0].save()
        self.assertEqual(len(learning.suggest(self.dev, self.manager)), 1)

    def test_a_low_competency_in_the_latest_published_report_suggests_training(self):
        report = PerformanceReport.objects.create(subject=self.dev, period="2026-Q4", overall_score=Decimal("70"), is_published=True)
        PerformanceCategoryScore.objects.create(report=report, category="technical", score=45, weight=30)
        PerformanceCategoryScore.objects.create(report=report, category="delivery", score=85, weight=20)
        PerformanceCategoryScore.objects.create(report=report, category="client", score=10, weight=10, not_applicable=True)
        created = learning.suggest(self.dev, self.manager)
        self.assertEqual([t.recommended_training for t in created], ["Advanced Engineering / Cloud Certification"])
        self.assertIn("Technical Competency", created[0].skill_gap)

    def test_an_unpublished_report_is_not_used(self):
        report = PerformanceReport.objects.create(subject=self.dev, period="2026-Q4", overall_score=Decimal("70"), is_published=False)
        PerformanceCategoryScore.objects.create(report=report, category="technical", score=10, weight=30)
        self.assertEqual(learning.suggest(self.dev, self.manager), [])

    def test_the_mapping_can_be_changed_by_a_setting_and_a_broken_one_is_ignored(self):
        self.goal("Leadership", 10)
        VertexSetting.objects.create(key=learning.SETTING_KEY, value=json.dumps({"goal": {"Leadership": ["Executive coaching", 6]}}))
        created = learning.suggest(self.dev, self.manager)
        self.assertEqual((created[0].recommended_training, created[0].duration_months), ("Executive coaching", 6))
        TrainingRecommendation.objects.all().delete()
        VertexSetting.objects.filter(key=learning.SETTING_KEY).update(value="{not json")
        created = learning.suggest(self.dev, self.manager)
        self.assertEqual(created[0].recommended_training, "Leadership & Mentoring Program")

    def test_an_unmapped_category_falls_back(self):
        self.goal("Something New", 0)
        self.assertEqual(learning.suggest(self.dev, self.manager)[0].recommended_training, learning.FALLBACK[0])

    def test_the_suggest_modal_runs_for_a_report_and_refuses_anyone_else(self):
        self.goal("Collaboration", 10)
        url = reverse("vertex-training-suggest")
        self.login(self.manager).post(url, {"employee": self.other.pk}, **HX)
        self.assertFalse(TrainingRecommendation.objects.exists())
        self.login(self.manager).post(url, {"employee": self.dev.pk}, **HX)
        self.assertEqual(TrainingRecommendation.objects.count(), 1)
        self.assertContains(self.login(self.dev).get(url, **HX), DENIED)
