from flask import Blueprint, render_template, request, redirect, url_for

from database import get_connection


employee_settings_bp = Blueprint("employee_settings", __name__)

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


@employee_settings_bp.route(
    "/employee-settings",
    methods=["GET", "POST"],
)
def manage_employee_settings():
    error = None
    connection = get_connection()

    try:
        if request.method == "POST":
            try:
                employee_id = int(request.form.get("employee_id", ""))
                weekly_off = int(request.form.get("weekly_off", ""))
                capacity = int(request.form.get("capacity", ""))

                if weekly_off not in range(7):
                    raise ValueError("Choose a valid weekly off.")

                if not 1 <= capacity <= 24:
                    raise ValueError(
                        "Daily capacity must be between 1 and 24 hours."
                    )

                employee = connection.execute(
                    "SELECT id FROM employees WHERE id = ?",
                    (employee_id,),
                ).fetchone()

                if employee is None:
                    raise ValueError("Employee not found.")

                connection.execute(
                    """
                    UPDATE employees
                    SET weekly_off = ?, daily_capacity_hours = ?
                    WHERE id = ?
                    """,
                    (weekly_off, capacity, employee_id),
                )
                connection.commit()

                return redirect(
                    url_for(
                        "employee_settings.manage_employee_settings",
                        saved="1",
                    )
                )

            except ValueError as exc:
                error = str(exc)

        employees = connection.execute(
            """
            SELECT
                employees.id,
                employees.name,
                employees.weekly_off,
                employees.daily_capacity_hours,
                pods.name AS pod_name
            FROM employees
            JOIN pods ON pods.id = employees.pod_id
            ORDER BY employees.id
            """
        ).fetchall()

        return render_template(
            "employee_settings.html",
            employees=employees,
            weekdays=WEEKDAYS,
            error=error,
            saved=request.args.get("saved") == "1",
        )

    finally:
        connection.close()
