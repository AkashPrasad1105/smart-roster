from datetime import date

from flask import Blueprint, render_template, request

from roster_engine import load_monthly_roster


coverage_bp = Blueprint("coverage", __name__)


@coverage_bp.route("/coverage")
def coverage_view():
    month = request.args.get(
        "month",
        date.today().strftime("%Y-%m"),
    )

    selected_brand = request.args.get("brand", "")
    selected_process = request.args.get("process", "")

    error = None
    roster = None
    brands = []
    processes = []
    rows = []

    try:
        roster = load_monthly_roster(month)
    except ValueError:
        error = "Please select a valid month."

    if roster:
        requirements = roster["requirements"]

        brands = sorted({
            item["brand_name"]
            for item in requirements
        })

        processes = sorted({
            item["process_name"]
            for item in requirements
        })

        employee_names = {
            employee["id"]: employee["name"]
            for employee in roster["employees"]
        }

        matching_requirements = [
            item
            for item in requirements
            if (
                not selected_brand
                or item["brand_name"] == selected_brand
            )
            and (
                not selected_process
                or item["process_name"] == selected_process
            )
        ]

        for date_text, day in sorted(roster["days"].items()):
            coverage_by_requirement = {
                item["requirement_id"]: item
                for item in day["coverage"]
            }

            for requirement in matching_requirements:
                coverage = coverage_by_requirement.get(
                    requirement["id"]
                )

                if coverage is None:
                    # The generator records coverage only for open processes.
                    rows.append({
                        "date": date_text,
                        "brand": requirement["brand_name"],
                        "process": requirement["process_name"],
                        "required": 0,
                        "assigned": 0,
                        "employees": "",
                        "status": "Closed",
                    })
                    continue

                names = [
                    employee_names.get(
                        employee_id,
                        f"Employee {employee_id}",
                    )
                    for employee_id in coverage["employee_ids"]
                ]

                status = (
                    "Covered"
                    if coverage["assigned"] >= coverage["required"]
                    else "Gap"
                )

                rows.append({
                    "date": date_text,
                    "brand": requirement["brand_name"],
                    "process": requirement["process_name"],
                    "required": coverage["required"],
                    "assigned": coverage["assigned"],
                    "employees": ", ".join(names),
                    "status": status,
                })

    gap_count = sum(row["status"] == "Gap" for row in rows)

    return render_template(
        "coverage.html",
        month=month,
        roster=roster,
        brands=brands,
        processes=processes,
        selected_brand=selected_brand,
        selected_process=selected_process,
        rows=rows,
        gap_count=gap_count,
        error=error,
    )
