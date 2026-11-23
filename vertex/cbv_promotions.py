"""
vertex/cbv_promotions.py

Promotions and Learning (training) on Ariel HR's list / filter / modal framework. The promotion
detail page and the training delete action are in vertex/views_promotions.py; the rules of the
promotion steps are in vertex/promotions.py and the suggestions in vertex/learning.py.
"""

from typing import Any

from django.contrib import messages
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from horilla.methods import handle_no_permission
from horilla_views.cbv_methods import login_required, permission_required
from horilla_views.generic.cbv.views import (
    HorillaFormView,
    HorillaListView,
    HorillaNavView,
    TemplateView,
)
from vertex import access, learning, promotions
from vertex.cbv import RELOAD_PAGE, VertexModal, _filter_indications
from vertex.cbv_appraisals import _create_attrs, _modal_attrs, _post_attrs
from vertex.filters import PromotionFilter, TrainingFilter
from vertex.forms_promotions import (
    DecisionForm,
    PromotionForm,
    RejectForm,
    SuggestForm,
    TrainingForm,
    TrainingStatusForm,
)
from vertex.models import (
    Promotion,
    PromotionStage,
    TrainingRecommendation,
    TrainingStatus,
)
from vertex.notify import notify_employees

# ---------------------------------------------------------------------------
# Promotions
# ---------------------------------------------------------------------------


def can_validate_row(request, instance, *args, **kwargs):
    return access.can_validate(request.user, instance)


def can_approve_row(request, instance, *args, **kwargs):
    return access.can_approve(request.user, instance)


def can_complete_row(request, instance, *args, **kwargs):
    return access.can_complete(request.user, instance)


def can_reject_row(request, instance, *args, **kwargs):
    return access.can_reject(request.user, instance)


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_promotion"), name="dispatch")
class PromotionsView(TemplateView):
    template_name = "vertex/cbv/promotions.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_promotion"), name="dispatch")
class PromotionsNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = PromotionFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["stage"]
    filter_body_template = "vertex/cbv/promotion_filter.html"
    hide_actions_button = True
    create_label = _("Recommend promotion")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-promotion-list")
        if access.recommendable_employees(self.request.user).exists():
            self.create_attrs = _create_attrs("vertex-promotion-new")


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_promotion"), name="dispatch")
class PromotionsList(HorillaListView):
    model = Promotion
    filter_class = PromotionFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_promotions(self.request.user).values("pk")).select_related(
            "employee", "recommended_by", "proposed_position"
        )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-promotion-list")

    @cached_property
    def columns(self):
        return [
            (_("Employee"), "get_employee_name"),
            (_("Designation"), "get_designation_display"),
            (_("Stage"), "get_stage_html"),
            (_("Recommended by"), "get_recommender_name"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Created"), "created_at")]

    row_status_indications = _filter_indications("stage", PromotionStage.choices)
    row_status_class = "status-{stage}"
    row_attrs = """ {redirect} """
    actions_menu = True
    actions = [
        {
            "action": _("Open"),
            "icon": "open-outline",
            "attrs": """onclick="event.preventDefault();window.location.href='{get_absolute_url}'" """,
        },
        {
            "action": _("Validate"),
            "icon": "checkmark-outline",
            "accessibility": "vertex.cbv_promotions.can_validate_row",
            "attrs": _modal_attrs("{get_validate_url}"),
        },
        {
            "action": _("Approve"),
            "icon": "checkmark-done-outline",
            "accessibility": "vertex.cbv_promotions.can_approve_row",
            "attrs": _modal_attrs("{get_approve_url}"),
        },
        {
            "action": _("Complete"),
            "icon": "ribbon-outline",
            "accessibility": "vertex.cbv_promotions.can_complete_row",
            "attrs": _modal_attrs("{get_complete_url}"),
        },
        {
            "action": _("Reject"),
            "icon": "close-circle-outline",
            "accessibility": "vertex.cbv_promotions.can_reject_row",
            "attrs": _modal_attrs("{get_reject_url}"),
        },
    ]


@method_decorator(login_required, name="dispatch")
class PromotionFormView(VertexModal, HorillaFormView):
    """Modal: put someone forward for promotion."""

    model = Promotion
    form_class = PromotionForm
    new_display_title = _("Recommend promotion")

    def dispatch(self, request, *args, **kwargs):
        if access.employee_of(request.user) is None or not access.recommendable_employees(request.user).exists():
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return None  # always a new recommendation

    def form_valid(self, form):
        promotions.recommend(form.save(commit=False), access.employee_of(self.request.user))
        messages.success(self.request, _("Promotion recommended. HR has been asked to validate it."))
        return self.HttpResponse(RELOAD_PAGE)


class _PromotionStepForm(VertexModal, HorillaFormView):
    """Base of the four step modals: who may open them is ``allowed``, what they do is ``run``."""

    model = Promotion
    form_class = DecisionForm
    success_message = ""

    @staticmethod
    def allowed(user, promotion):
        return False

    def run(self, promotion_id, actor, note):
        raise NotImplementedError

    def dispatch(self, request, *args, **kwargs):
        promotion = access.visible_promotions(request.user).filter(pk=kwargs.get("pk")).first()
        if promotion is None or not self.allowed(request.user, promotion):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            self.run(self.kwargs["pk"], access.employee_of(self.request.user), form.cleaned_data.get("note", ""))
        except promotions.PromotionError as error:
            messages.error(self.request, str(error))
        else:
            messages.success(self.request, self.success_message)
        return self.HttpResponse(RELOAD_PAGE)


@method_decorator(login_required, name="dispatch")
class PromotionValidateFormView(_PromotionStepForm):
    allowed = staticmethod(access.can_validate)
    success_message = _("Promotion validated. Leadership has been asked to approve it.")

    def run(self, promotion_id, actor, note):
        promotions.validate(promotion_id, actor, note)


@method_decorator(login_required, name="dispatch")
class PromotionApproveFormView(_PromotionStepForm):
    allowed = staticmethod(access.can_approve)
    success_message = _("Promotion approved. HR can now complete it.")

    def run(self, promotion_id, actor, note):
        promotions.approve(promotion_id, actor, note)


@method_decorator(login_required, name="dispatch")
class PromotionCompleteFormView(_PromotionStepForm):
    allowed = staticmethod(access.can_complete)
    success_message = _("Promotion completed. The employee's designation is updated.")

    def run(self, promotion_id, actor, note):
        promotions.complete(promotion_id, actor, note)


@method_decorator(login_required, name="dispatch")
class PromotionRejectFormView(_PromotionStepForm):
    form_class = RejectForm
    allowed = staticmethod(access.can_reject)
    success_message = _("Promotion rejected.")

    def run(self, promotion_id, actor, note):
        promotions.reject(promotion_id, actor, note)


# ---------------------------------------------------------------------------
# Learning (training)
# ---------------------------------------------------------------------------


def can_update_training_row(request, instance, *args, **kwargs):
    return access.can_update_training_status(request.user, instance)


def can_edit_training_row(request, instance, *args, **kwargs):
    return access.can_edit_training(request.user, instance)


def can_delete_training_row(request, instance, *args, **kwargs):
    return access.can_delete_training(request.user, instance)


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_trainingrecommendation"), name="dispatch")
class TrainingView(TemplateView):
    template_name = "vertex/cbv/training.html"


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_trainingrecommendation"), name="dispatch")
class TrainingNavView(HorillaNavView):
    filter_form_context_name = "form"
    filter_instance = TrainingFilter()
    search_swap_target = "#listContainer"
    group_by_fields = ["status", "source", "employee"]
    filter_body_template = "vertex/cbv/training_filter.html"
    hide_actions_button = True
    create_label = _("Assign training")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-training-list")
        if access.training_employees(self.request.user).exists():
            self.create_attrs = _create_attrs("vertex-training-new")
        if self.request.user.has_perm("vertex.add_trainingrecommendation"):
            self.extra_buttons = (
                '<button type="button" class="px-4 py-2 border border-gray-300 text-gray-700 rounded-md text-xs flex items-center gap-2 hover:bg-gray-100 transition duration-300 cursor-pointer" '
                f'hx-get="{reverse("vertex-training-suggest")}" hx-target="#genericModalBody" '
                'data-toggle="oh-modal-toggle" data-target="#genericModal">'
                '<ion-icon name="sparkles-outline"></ion-icon>'
                f'{_("Suggest training")}</button>'
            )


@method_decorator(login_required, name="dispatch")
@method_decorator(permission_required("vertex.view_trainingrecommendation"), name="dispatch")
class TrainingList(HorillaListView):
    model = TrainingRecommendation
    filter_class = TrainingFilter
    bulk_select_option = False
    quick_export = False

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(pk__in=access.visible_training(self.request.user).values("pk")).select_related(
            "employee", "created_by"
        )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.search_url = reverse("vertex-training-list")

    @cached_property
    def columns(self):
        return [
            (_("Employee"), "get_employee_name"),
            (_("Skill gap"), "skill_gap"),
            (_("Training"), "recommended_training"),
            (_("Duration"), "get_duration_display"),
            (_("Status"), "get_status_html"),
            (_("Source"), "get_source_display"),
        ]

    @cached_property
    def sortby_mapping(self):
        return [(_("Created"), "created_at"), (_("Duration"), "duration_months")]

    row_status_indications = _filter_indications("status", TrainingStatus.choices)
    row_status_class = "status-{status}"
    actions_menu = True
    actions = [
        {
            "action": _("Update status"),
            "icon": "trending-up-outline",
            "accessibility": "vertex.cbv_promotions.can_update_training_row",
            "attrs": _modal_attrs("{get_status_url}"),
        },
        {
            "action": _("Edit"),
            "icon": "create-outline",
            "accessibility": "vertex.cbv_promotions.can_edit_training_row",
            "attrs": _modal_attrs("{get_update_url}"),
        },
        {
            "action": _("Delete"),
            "icon": "trash-outline",
            "accessibility": "vertex.cbv_promotions.can_delete_training_row",
            "attrs": _post_attrs("{get_delete_url}", "Delete this training? This cannot be undone."),
        },
    ]


@method_decorator(login_required, name="dispatch")
class TrainingFormView(VertexModal, HorillaFormView):
    model = TrainingRecommendation
    form_class = TrainingForm
    new_display_title = _("Assign training")

    def dispatch(self, request, *args, **kwargs):
        pk = kwargs.get("pk")
        if pk:
            training = access.visible_training(request.user).filter(pk=pk).first()
            if training is None or not access.can_edit_training(request.user, training):
                return handle_no_permission(request)
        elif not access.training_employees(request.user).exists():
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.form.instance.pk:
            self.form_class.verbose_name = _("Edit training")
        return context

    def form_valid(self, form):
        creating = form.instance.pk is None
        me = access.employee_of(self.request.user)
        training = form.save(commit=False)
        if creating:
            training.created_by = me
        training.save()
        if creating:
            notify_employees(
                me, [training.employee],
                str(_("New training assigned: %(name)s (%(n)s months)"))
                % {"name": training.recommended_training, "n": training.duration_months},
                reverse("vertex-training"), icon="school-outline",
            )
            messages.success(self.request, _("Training assigned."))
        else:
            messages.success(self.request, _("Training updated."))
        return self.HttpResponse(RELOAD_PAGE)


@method_decorator(login_required, name="dispatch")
class TrainingStatusFormView(VertexModal, HorillaFormView):
    model = TrainingRecommendation
    form_class = TrainingStatusForm

    def dispatch(self, request, *args, **kwargs):
        training = access.visible_training(request.user).filter(pk=kwargs.get("pk")).first()
        if training is None or not access.can_update_training_status(request.user, training):
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        form.save()
        messages.success(self.request, _("Training updated."))
        return self.HttpResponse(RELOAD_PAGE)


@method_decorator(login_required, name="dispatch")
class TrainingSuggestFormView(VertexModal, HorillaFormView):
    """Modal: look at one person's goals and published report and suggest training for the gaps."""

    model = TrainingRecommendation
    form_class = SuggestForm
    new_display_title = _("Suggest training")

    def dispatch(self, request, *args, **kwargs):
        if not access.training_employees(request.user).exists():
            return handle_no_permission(request)
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return None

    def form_valid(self, form):
        employee = form.cleaned_data["employee"]
        created = learning.suggest(employee, access.employee_of(self.request.user))
        if created:
            messages.success(
                self.request,
                _("%(n)s training suggestion(s) added for %(name)s.") % {"n": len(created), "name": employee.get_full_name()},
            )
        else:
            messages.info(
                self.request,
                _("No new skill gap found for %(name)s: goals and the latest report are above 60%%, or the gaps already have training.")
                % {"name": employee.get_full_name()},
            )
        return self.HttpResponse(RELOAD_PAGE)
