import json
from datetime import date

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
)

from database import get_connection
from roster_engine import load_monthly_roster


roster_edit_bp = Blueprint("roster_edit", __name__)

EDIT_STATUSES = [
    "WORK",
    "AVAILABLE",
    "WEEKLY_OFF",
    "LEAVE",
    "COMP_OFF",
    "UNAVAILABLE",
]


def refresh_saved_coverage(roster):
    """Update saved coverage counts after a manual edit.

    This describes the saved plan. The separate validator checks
    whether these assignments satisfy current operational rules.
    """
    gaps = []

    for day_text, day in roster["days"].items():
        for coverage in day["coverage"]:
            requirement_id = coverage["requirement_id"]

            employee_ids = [
                int(employee_key)
                for employee_key, entry in day["employees"].items()
                if entry["status"] == "WORK"
                and any(
                    job["requirement_id"] == requirement_id
                    for job in entry["jobs"]
                )
            ]

            coverage["employee_ids"] = employee_ids
            coverage["assigned"] = len(employee_ids)

            if coverage["assigned"] < coverage["required"]:
                gaps.append({
                    "date": day_text,
                    **coverage,
                    "reasons": [
                        "Insufficient staff in the edited plan. "
                        "Open live validation for current-rule checks."
                    ],
                })

    roster["gaps"] = gaps


@roster_edit_bp.route("/roster/edit", methods=["GET", "POST"])
def edit_day():
    values = request.form if request.method == "POST" else request.args

    month = values.get("month", date.today().strftime("%Y-%m"))
    employee_key = values.get("employee", "")
    day_text = values.get("day", "")

    error = None
    roster = None
    employee = None
    entry = None
    choices = []

    try:
        roster = load_monthly_roster(month)

        if roster is None:
            raise ValueError("Generate this month's roster first.")

        employee_id = int(employee_key)

        employee = next(
            (
                item for item in roster["employees"]
                if item["id"] == employee_id
            ),
            None,
        )

        if employee is None:
            raise ValueError("Employee is not in this saved roster.")

        if day_text not in roster["days"]:
            raise ValueError("Choose a date within the saved month.")

        entry = roster["days"][day_text]["employees"].get(employee_key)

        if entry is None:
            raise ValueError("No saved entry exists for this employee.")

        connection = get_connection()

        try:
            eligible_ids = {
                row["brand_process_id"]
                for row in connection.execute("""
                    SELECT brand_process_id
                    FROM assignments
                    WHERE employee_id = ?
                """, (employee_id,)).fetchall()
            }
        finally:
            connection.close()

        open_ids = {
            item["requirement_id"]
            for item in roster["days"][day_text]["coverage"]
        }

        choices = [
            item
            for item in roster["requirements"]
            if item["id"] in eligible_ids
            and item["id"] in open_ids
        ]

        if request.method == "POST":
            status = request.form.get("status", "")
            selected_ids = {
                int(value)
                for value in request.form.getlist("jobs")
            }

            if status not in EDIT_STATUSES:
                raise ValueError("Choose a valid status.")

            allowed_ids = {item["id"] for item in choices}

            if not selected_ids.issubset(allowed_ids):
                raise ValueError("One or more responsibilities are invalid.")

            if status != "WORK" and selected_ids:
                raise ValueError(
                    "Uncheck all responsibilities when choosing "
                    "a status other than Work."
                )

            if status == "WORK" and not selected_ids:
                raise ValueError(
                    "Select at least one responsibility for Work."
                )

            selected_requirements = [
                item for item in choices
                if item["id"] in selected_ids
            ]

            jobs = [
                {
                    "requirement_id": item["id"],
                    "brand": item["brand_name"],
                    "process": item["process_name"],
                    "hours": item["hours_per_employee"],
                }
                for item in selected_requirements
            ]

            roster["days"][day_text]["employees"][employee_key] = {
                "status": status,
                "hours": sum(job["hours"] for job in jobs),
                "jobs": jobs,
                "locked": True,
            }

            refresh_saved_coverage(roster)

            connection = get_connection()

            try:
                with connection:
                    connection.execute("""
                        UPDATE monthly_rosters
                        SET roster_json = ?
                        WHERE month = ?
                    """, (json.dumps(roster), month))
            finally:
                connection.close()

            return redirect(
                url_for("validation.validation_view", month=month)
            )

    except ValueError as exc:
        error = str(exc)

    selected_jobs = {
        job["requirement_id"]
        for job in entry["jobs"]
    } if entry else set()

    return render_template(
        "roster_edit.html",
        month=month,
        day_text=day_text,
        employee_key=employee_key,
        employee=employee,
        entry=entry,
        choices=choices,
        statuses=EDIT_STATUSES,
        selected_jobs=selected_jobs,
        error=error,
    )
