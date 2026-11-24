"""
vertex/analytics.py

Talent analytics for HR and leadership (appraisal completion, rating spread, department
performance, promotion and training reports, the 9-box matrix) and the payroll CSV of completed
promotions. Pure functions over the models; the pages are in vertex/views_dashboard.py.

The 9-box puts each person's latest final rating (performance) against the average progress of
their goals (delivery), three bands each, so a person lands in one of nine boxes.
"""

import csv
import io
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal

from vertex.models import (
    Appraisal,
    AppraisalStage,
    Goal,
    GoalStatus,
    Promotion,
    PromotionStage,
    TrainingRecommendation,
    TrainingStatus,
)

DONE_STAGES = (AppraisalStage.MANAGER_COMPLETED, AppraisalStage.RELEASED)
TOP_GAPS = 8

# (label, lowest rating in the bucket), highest first
RATING_BUCKETS = [
    ("Outstanding (4.5 - 5)", Decimal("4.5")),
    ("Excellent (4 - 4.4)", Decimal("4")),
    ("Good (3 - 3.9)", Decimal("3")),
    ("Needs improvement (below 3)", Decimal("0")),
]

# rows: performance band (high first); columns: delivery band (low to high)
NINE_BOX_LABELS = [
    ["Rough diamond", "High performer", "Star"],
    ["Under-performer", "Core player", "Strong performer"],
    ["Risk", "Inconsistent player", "Effective"],
]
PERFORMANCE_BANDS = ("High", "Medium", "Low")
DELIVERY_BANDS = ("Low", "Medium", "High")


def _band_performance(rating):
    return 0 if rating >= 4 else 1 if rating >= 3 else 2


def _band_delivery(progress):
    return 0 if progress < 40 else 1 if progress < 70 else 2


def box_label(rating, progress):
    return NINE_BOX_LABELS[_band_performance(rating)][_band_delivery(progress)]


def _department_name(employee):
    info = getattr(employee, "employee_work_info", None)
    department = getattr(info, "department_id", None)
    return department.department if department else "Unassigned"


def _round(value, places=1):
    return float(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def completion():
    total = Appraisal.objects.count()
    done = Appraisal.objects.filter(stage__in=DONE_STAGES).count()
    return {
        "total": total,
        "completed": done,
        "pending": total - done,
        "rate": round(100 * done / total) if total else 0,
    }


def rating_distribution():
    ratings = list(
        Appraisal.objects.filter(final_rating__isnull=False).values_list("final_rating", flat=True)
    )
    result = []
    for index, (label, floor) in enumerate(RATING_BUCKETS):
        ceiling = RATING_BUCKETS[index - 1][1] if index else None
        count = sum(1 for r in ratings if r >= floor and (ceiling is None or r < ceiling))
        result.append({"label": label, "count": count})
    return result


def latest_ratings():
    """{employee id: (employee, latest final rating)} over rated appraisals, newest cycle first."""
    latest = {}
    appraisals = (
        Appraisal.objects.filter(final_rating__isnull=False)
        .select_related("employee", "employee__employee_work_info__department_id", "cycle")
        .order_by("-cycle__start_date", "-pk")
    )
    for appraisal in appraisals:
        latest.setdefault(appraisal.employee_id, (appraisal.employee, appraisal.final_rating))
    return latest


def department_performance():
    totals = defaultdict(list)
    for employee, rating in latest_ratings().values():
        totals[_department_name(employee)].append(rating)
    rows = [
        {"department": name, "rating": _round(sum(values) / len(values), 2), "count": len(values)}
        for name, values in totals.items()
    ]
    return sorted(rows, key=lambda row: (-row["rating"], row["department"]))


def promotion_report():
    counts = Counter(Promotion.objects.values_list("stage", flat=True))
    return [{"stage": label, "count": counts.get(value, 0)} for value, label in PromotionStage.choices]


def training_report():
    trainings = TrainingRecommendation.objects.all()
    by_status = Counter(trainings.values_list("status", flat=True))
    gaps = Counter(trainings.values_list("skill_gap", flat=True))
    return {
        "by_status": [{"status": label, "count": by_status.get(value, 0)} for value, label in TrainingStatus.choices],
        "by_gap": [{"gap": gap, "count": count} for gap, count in gaps.most_common(TOP_GAPS)],
    }


def goal_progress():
    """{employee id: average progress of their live goals}"""
    progress = defaultdict(list)
    for employee_id, value in Goal.objects.exclude(status=GoalStatus.CANCELLED).values_list("employee_id", "progress"):
        progress[employee_id].append(value)
    return {key: sum(values) / len(values) for key, values in progress.items()}


def nine_box():
    """Everyone with a final rating, placed in one of nine boxes; people without goals count as 0% delivery."""
    progress = goal_progress()
    people = []
    cells = [[[] for _ in range(3)] for _ in range(3)]
    for employee_id, (employee, rating) in latest_ratings().items():
        delivery = progress.get(employee_id, 0)
        person = {
            "name": employee.get_full_name(),
            "department": _department_name(employee),
            "rating": _round(rating, 1),
            "progress": round(delivery),
            "box": box_label(rating, delivery),
        }
        people.append(person)
        cells[_band_performance(rating)][_band_delivery(delivery)].append(person)
    grid = [
        {
            "performance": PERFORMANCE_BANDS[row],
            "cells": [
                {"label": NINE_BOX_LABELS[row][col], "people": sorted(cells[row][col], key=lambda p: p["name"])}
                for col in range(3)
            ],
        }
        for row in range(3)
    ]
    return {"grid": grid, "delivery_bands": DELIVERY_BANDS, "count": len(people), "people": people}


def talent_report():
    return {
        "completion": completion(),
        "ratings": rating_distribution(),
        "departments": department_performance(),
        "promotions": promotion_report(),
        "training": training_report(),
        "nine_box": nine_box(),
    }


# ---------------------------------------------------------------------------
# Payroll export
# ---------------------------------------------------------------------------

PAYROLL_HEADER = [
    "Employee ID", "Employee", "New designation", "Current salary", "Revised salary",
    "Increment %", "Effective date",
]


def increment_percent(current, proposed):
    """(proposed - current) / current as a percentage with one decimal, or None when it cannot be worked out."""
    if not current or proposed is None:
        return None
    return ((Decimal(proposed) - Decimal(current)) / Decimal(current) * 100).quantize(
        Decimal("0.1"), rounding=ROUND_HALF_UP
    )


def _safe_cell(value):
    """Stop a spreadsheet from running a cell that starts like a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def payroll_csv():
    promotions = (
        Promotion.objects.filter(stage=PromotionStage.COMPLETED)
        .select_related("employee", "proposed_position")
        .order_by("completed_at", "pk")
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(PAYROLL_HEADER)
    for promotion in promotions:
        percent = increment_percent(promotion.current_salary, promotion.proposed_salary)
        writer.writerow(
            [
                promotion.employee.badge_id or promotion.employee_id,
                _safe_cell(promotion.employee.get_full_name()),
                _safe_cell(promotion.proposed_position.job_position),
                "" if promotion.current_salary is None else promotion.current_salary,
                "" if promotion.proposed_salary is None else promotion.proposed_salary,
                "" if percent is None else percent,
                promotion.completed_at.date().isoformat() if promotion.completed_at else "",
            ]
        )
    return buffer.getvalue()
