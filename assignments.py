import sqlite3

from flask import Blueprint, render_template, request, redirect, url_for

from database import get_connection, get_employees, get_brand_processes


assignments_bp = Blueprint("assignments", __name__)


@assignments_bp.route("/assignments", methods=["GET", "POST"])
def manage_assignments():
    connection = get_connection()
    error = None

    try:
        if request.method == "POST":
            try:
                employee_id = int(request.form["employee_id"])
                requirement_id = int(request.form["requirement_id"])
                action = request.form["action"]

                with connection:
                    employee = connection.execute(
                        "SELECT id FROM employees WHERE id = ?",
                        (employee_id,),
                    ).fetchone()

                    requirement = connection.execute(
                        "SELECT id FROM brand_processes WHERE id = ?",
                        (requirement_id,),
                    ).fetchone()

                    if employee is None or requirement is None:
                        raise ValueError("Invalid employee or requirement.")

                    if action == "add":
                        connection.execute("""
                            INSERT INTO assignments (
                                employee_id,
                                brand_process_id
                            )
                            VALUES (?, ?)
                            ON CONFLICT(employee_id, brand_process_id)
                            DO NOTHING
                        """, (employee_id, requirement_id))

                    elif action == "remove":
                        connection.execute("""
                            DELETE FROM assignments
                            WHERE employee_id = ?
                              AND brand_process_id = ?
                        """, (employee_id, requirement_id))

                    else:
                        raise ValueError("Invalid action.")

                return redirect(
                    url_for("assignments.manage_assignments")
                )

            except (ValueError, KeyError, sqlite3.IntegrityError):
                error = (
                    "Unable to update the assignment. "
                    "Choose a valid employee and brand/process."
                )

        allocations = connection.execute("""
            SELECT
                assignments.employee_id,
                assignments.brand_process_id AS requirement_id,
                employees.name AS employee_name,
                brands.name AS brand_name,
                processes.name AS process_name
            FROM assignments
            JOIN employees
                ON employees.id = assignments.employee_id
            JOIN brand_processes AS bp
                ON bp.id = assignments.brand_process_id
            JOIN brands
                ON brands.id = bp.brand_id
            JOIN processes
                ON processes.id = bp.process_id
            ORDER BY brands.id, processes.id, employees.name
        """).fetchall()

        return render_template(
            "assignments.html",
            employees=get_employees(),
            requirements=get_brand_processes(),
            allocations=allocations,
            error=error,
        )
    finally:
        connection.close()
