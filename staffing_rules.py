"""Date-specific minimum staffing; operating calendars still take precedence."""
from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for
from database import get_connection

staffing_bp = Blueprint("staffing", __name__)


def initialise_staffing_storage(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS staffing_overrides (
            brand_process_id INTEGER NOT NULL REFERENCES brand_processes(id),
            work_date TEXT NOT NULL,
            minimum_staff INTEGER NOT NULL CHECK (minimum_staff >= 1),
            PRIMARY KEY (brand_process_id, work_date)
        )
    """)
    connection.commit()


def load_staffing_overrides(connection):
    initialise_staffing_storage(connection)
    return {
        (row["brand_process_id"], row["work_date"]): row["minimum_staff"]
        for row in connection.execute("SELECT * FROM staffing_overrides")
    }


def requirements_for_date(requirements, day_text, overrides):
    # Copies prevent one date's override from leaking into subsequent dates.
    return [
        dict(requirement, minimum_staff=overrides.get(
            (requirement["id"], day_text), requirement["minimum_staff"]
        ))
        for requirement in requirements
    ]


@staffing_bp.route("/staffing-overrides", methods=["GET", "POST"])
def manage_overrides():
    connection = get_connection()
    error = None
    try:
        initialise_staffing_storage(connection)
        if request.method == "POST":
            try:
                requirement_id = int(request.form.get("requirement_id", ""))
                day_text = date.fromisoformat(
                    request.form.get("work_date", "")
                ).isoformat()
                action = request.form.get("action", "")
                if action not in ("save", "delete"):
                    raise ValueError("Choose a valid action.")
                if connection.execute(
                    "SELECT id FROM brand_processes WHERE id = ?",
                    (requirement_id,),
                ).fetchone() is None:
                    raise ValueError("Choose an existing brand and process.")
                if action == "save":
                    minimum = int(request.form.get("minimum_staff", ""))
                    if minimum < 1:
                        raise ValueError("Minimum staff must be at least 1.")
                    connection.execute("""
                        INSERT INTO staffing_overrides
                            (brand_process_id, work_date, minimum_staff)
                        VALUES (?, ?, ?)
                        ON CONFLICT(brand_process_id, work_date)
                        DO UPDATE SET minimum_staff = excluded.minimum_staff
                    """, (requirement_id, day_text, minimum))
                else:
                    connection.execute("""
                        DELETE FROM staffing_overrides
                        WHERE brand_process_id = ? AND work_date = ?
                    """, (requirement_id, day_text))
                connection.commit()
                return redirect(url_for("staffing.manage_overrides", saved="1"))
            except ValueError as exc:
                error = str(exc)

        requirements = connection.execute("""
            SELECT bp.id, bp.minimum_staff, b.name AS brand_name,
                   p.name AS process_name
            FROM brand_processes bp
            JOIN brands b ON b.id = bp.brand_id
            JOIN processes p ON p.id = bp.process_id
            ORDER BY b.name, p.name
        """).fetchall()
        overrides = connection.execute("""
            SELECT o.*, b.name AS brand_name, p.name AS process_name
            FROM staffing_overrides o
            JOIN brand_processes bp ON bp.id = o.brand_process_id
            JOIN brands b ON b.id = bp.brand_id
            JOIN processes p ON p.id = bp.process_id
            ORDER BY o.work_date, b.name, p.name
        """).fetchall()
        return render_template(
            "staffing_overrides.html", requirements=requirements,
            overrides=overrides, error=error,
            saved=request.args.get("saved") == "1",
        )
    finally:
        connection.close()
