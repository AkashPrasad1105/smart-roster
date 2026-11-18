from datetime import date
import sqlite3

from flask import Blueprint, render_template, request, redirect, url_for

from database import get_connection, get_employees

availability_bp = Blueprint("availability", __name__)


def initialise_availability():
    connection = get_connection()

    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS absences (
                id INTEGER PRIMARY KEY,
                employee_id INTEGER NOT NULL,
                absence_date TEXT NOT NULL,
                absence_type TEXT NOT NULL CHECK (
                    absence_type IN ('LEAVE', 'COMP_OFF', 'UNAVAILABLE')
                ),
                UNIQUE (employee_id, absence_date),
                FOREIGN KEY (employee_id) REFERENCES employees(id)
            );

            CREATE TABLE IF NOT EXISTS holidays (
                holiday_date TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
        """)
        connection.commit()
    finally:
        connection.close()


@availability_bp.route("/availability", methods=["GET", "POST"])
def manage_availability():
    error = None
    connection = get_connection()

    try:
        if request.method == "POST":
            try:
                action = request.form.get("action")

                with connection:
                    if action == "add_absence":
                        employee_id = int(request.form["employee_id"])
                        absence_date = date.fromisoformat(
                            request.form["absence_date"]
                        ).isoformat()
                        absence_type = request.form["absence_type"]

                        if absence_type not in (
                            "LEAVE", "COMP_OFF", "UNAVAILABLE"
                        ):
                            raise ValueError("Invalid absence type.")

                        employee = connection.execute(
                            "SELECT id FROM employees WHERE id = ?",
                            (employee_id,),
                        ).fetchone()

                        if employee is None:
                            raise ValueError("Employee does not exist.")

                        connection.execute("""
                            INSERT INTO absences (
                                employee_id,
                                absence_date,
                                absence_type
                            )
                            VALUES (?, ?, ?)
                            ON CONFLICT(employee_id, absence_date)
                            DO UPDATE SET
                                absence_type = excluded.absence_type
                        """, (employee_id, absence_date, absence_type))

                    elif action == "add_holiday":
                        holiday_date = date.fromisoformat(
                            request.form["holiday_date"]
                        ).isoformat()
                        name = request.form["name"].strip()

                        if not name or len(name) > 100:
                            raise ValueError(
                                "Holiday name must contain 1–100 characters."
                            )

                        connection.execute("""
                            INSERT INTO holidays (holiday_date, name)
                            VALUES (?, ?)
                            ON CONFLICT(holiday_date)
                            DO UPDATE SET name = excluded.name
                        """, (holiday_date, name))

                    elif action == "delete_absence":
                        connection.execute(
                            "DELETE FROM absences WHERE id = ?",
                            (int(request.form["absence_id"]),),
                        )

                    elif action == "delete_holiday":
                        connection.execute(
                            "DELETE FROM holidays WHERE holiday_date = ?",
                            (request.form["holiday_date"],),
                        )

                    else:
                        raise ValueError("Unknown action.")

                return redirect(
                    url_for("availability.manage_availability")
                )

            except (ValueError, KeyError, sqlite3.IntegrityError):
                error = (
                    "Unable to save. Check the employee, date and "
                    "required fields, then try again."
                )

        absences = connection.execute("""
            SELECT
                absences.id,
                employees.name AS employee_name,
                absences.absence_date,
                absences.absence_type
            FROM absences
            JOIN employees ON employees.id = absences.employee_id
            ORDER BY absences.absence_date, employees.name
        """).fetchall()

        holidays = connection.execute("""
            SELECT holiday_date, name
            FROM holidays
            ORDER BY holiday_date
        """).fetchall()

        return render_template(
            "availability.html",
            employees=get_employees(),
            absences=absences,
            holidays=holidays,
            error=error,
        )
    finally:
        connection.close()
