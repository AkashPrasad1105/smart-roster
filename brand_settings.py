import math

from flask import Blueprint, render_template, request, redirect, url_for

from database import get_connection


brand_settings_bp = Blueprint("brand_settings", __name__)

CALENDARS = {
    "EVERY_DAY": "Every day, including holidays",
    "HOLIDAYS_OFF": "Every day except public holidays",
    "SUNDAYS_HOLIDAYS_OFF": "Closed on Sundays and public holidays",
}


@brand_settings_bp.route("/brand-settings", methods=["GET", "POST"])
def manage_brand_settings():
    error = None
    connection = get_connection()

    try:
        if request.method == "POST":
            try:
                requirement_id = int(
                    request.form.get("requirement_id", "")
                )
                calendar = request.form.get("calendar", "")
                minimum_staff = int(
                    request.form.get("minimum_staff", "")
                )
                hours = float(request.form.get("hours", ""))

                if calendar not in CALENDARS:
                    raise ValueError("Choose a valid operating calendar.")

                if minimum_staff < 1:
                    raise ValueError("Minimum staff must be at least 1.")

                if not math.isfinite(hours) or not 0 < hours <= 24:
                    raise ValueError(
                        "Hours must be greater than 0 and at most 24."
                    )

                requirement = connection.execute(
                    "SELECT id FROM brand_processes WHERE id = ?",
                    (requirement_id,),
                ).fetchone()

                if requirement is None:
                    raise ValueError("Brand/process requirement not found.")

                connection.execute(
                    """
                    UPDATE brand_processes
                    SET calendar = ?,
                        minimum_staff = ?,
                        hours_per_employee = ?
                    WHERE id = ?
                    """,
                    (
                        calendar,
                        minimum_staff,
                        hours,
                        requirement_id,
                    ),
                )
                connection.commit()

                return redirect(
                    url_for(
                        "brand_settings.manage_brand_settings",
                        saved="1",
                    )
                )

            except ValueError as exc:
                error = str(exc)

        requirements = connection.execute(
            """
            SELECT
                bp.id,
                brands.name AS brand_name,
                processes.name AS process_name,
                bp.calendar,
                bp.minimum_staff,
                bp.hours_per_employee
            FROM brand_processes AS bp
            JOIN brands ON brands.id = bp.brand_id
            JOIN processes ON processes.id = bp.process_id
            ORDER BY brands.name, processes.name
            """
        ).fetchall()

        return render_template(
            "brand_settings.html",
            requirements=requirements,
            calendars=CALENDARS,
            error=error,
            saved=request.args.get("saved") == "1",
        )

    finally:
        connection.close()
