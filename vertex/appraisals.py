"""
vertex/appraisals.py

Appraisal cycle rules ported from ArielVertex (Cycles / Appraisals controllers), with the gaps fixed:

* a cycle is activated once (Draft -> Active) and closed once (Active -> Closed); ArielVertex let a
  closed cycle be activated again and let ratings be added to a closed cycle;
* activation opens one appraisal per active employee and never duplicates one, so it is safe to
  repeat on an active cycle to pick up people who joined later;
* each appraisal records the reporting manager that applied when the cycle opened.
"""

from django.db import transaction
from django.urls import reverse
from django.utils.translation import gettext as _t

from employee.models import Employee
from vertex.models import Appraisal, AppraisalStage, CycleStatus
from vertex.notify import notify_employees


@transaction.atomic
def activate_cycle(cycle, by=None):
    """Open the cycle and give every active employee without an appraisal one; returns the new appraisals."""
    if cycle.status == CycleStatus.CLOSED:
        raise ValueError(_t("A closed cycle cannot be opened again."))
    existing = set(cycle.appraisals.values_list("employee_id", flat=True))
    created = []
    people = Employee.objects.filter(is_active=True).select_related("employee_work_info")
    for employee in people:
        if employee.pk in existing:
            continue
        manager = employee.get_reporting_manager()
        created.append(
            Appraisal(cycle=cycle, employee=employee, manager=manager, stage=AppraisalStage.SELF_PENDING)
        )
    for appraisal in created:
        appraisal.save()  # one by one: bulk_create skips the audit trail
    cycle.status = CycleStatus.ACTIVE
    cycle.save(update_fields=["status"])
    notify_employees(
        by,
        [appraisal.employee for appraisal in created],
        _t("'%(cycle)s' is open. Please submit your self assessment.") % {"cycle": cycle.name},
        reverse("vertex-appraisals"),
        icon="ribbon-outline",
    )
    return created


def close_cycle(cycle):
    if cycle.status != CycleStatus.ACTIVE:
        raise ValueError(_t("Only an active cycle can be closed."))
    cycle.status = CycleStatus.CLOSED
    cycle.save(update_fields=["status"])
    return cycle
