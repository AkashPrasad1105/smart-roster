import csv
import io
from flask import Flask, render_template, request, redirect, url_for, Response, flash
from validation import validation_bp, validate_saved_roster
from roster_edit import roster_edit_bp
from employee_settings import employee_settings_bp
from brand_settings import brand_settings_bp
from staffing_rules import staffing_bp
from simulator import simulator_bp

from database import (
    initialise_database,
    initialise_brand_data,
    get_employees,
    get_brand_processes,
    get_pods,
    get_processes,
    add_employee,
    add_brand,
)

from availability import (
    availability_bp,
    initialise_availability,
)

from assignments import assignments_bp
from roster import roster_bp
from coverage import coverage_bp
from roster_engine import initialise_roster_storage, load_monthly_roster


app = Flask(__name__)
app.secret_key = "smart-roster-datastraw-assessment-key"

initialise_database()
initialise_brand_data()
initialise_availability()
initialise_roster_storage()

app.register_blueprint(availability_bp)
app.register_blueprint(assignments_bp)
app.register_blueprint(roster_bp)
app.register_blueprint(coverage_bp)
app.register_blueprint(validation_bp)
app.register_blueprint(roster_edit_bp)
app.register_blueprint(employee_settings_bp)
app.register_blueprint(brand_settings_bp)
app.register_blueprint(staffing_bp)
app.register_blueprint(simulator_bp)


@app.route("/")
def home():
    pods = get_pods()
    employees = get_employees()
    requirements = get_brand_processes()

    # Calculate system metrics
    total_employees = len(employees)
    total_pods = len(pods)
    unique_brands = len({r["brand_name"] for r in requirements})
    unique_processes = len({r["process_name"] for r in requirements})

    return render_template(
        "index.html",
        total_employees=total_employees,
        total_pods=total_pods,
        unique_brands=unique_brands,
        unique_processes=unique_processes,
        pods=pods,
    )


@app.route("/employees", methods=["GET", "POST"])
def employees():
    weekday_names = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]

    error = None
    success = None

    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            pod_id = int(request.form.get("pod_id", 1))
            is_pod_lead = 1 if request.form.get("is_pod_lead") else 0
            weekly_off = int(request.form.get("weekly_off", 6))
            capacity = int(request.form.get("capacity", 8))

            if not name:
                raise ValueError("Employee name is required.")
            if weekly_off not in range(7):
                raise ValueError("Invalid weekly off selection.")
            if not 1 <= capacity <= 24:
                raise ValueError("Daily capacity must be 1–24 hours.")

            add_employee(name, pod_id, is_pod_lead, weekly_off, capacity)
            success = f"Successfully added {name} to team."
        except Exception as exc:
            error = str(exc)

    return render_template(
        "employees.html",
        employees=get_employees(),
        pods=get_pods(),
        weekday_names=weekday_names,
        error=error,
        success=success,
    )


@app.route("/brands", methods=["GET", "POST"])
def brands():
    calendar_labels = {
        "EVERY_DAY": "Every day, including holidays (365 days)",
        "HOLIDAYS_OFF": "Every day except public holidays",
        "SUNDAYS_HOLIDAYS_OFF": "Sundays and public holidays off",
    }

    error = None
    success = None

    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                raise ValueError("Brand name is required.")

            cs_calendar = request.form.get("cs_calendar", "EVERY_DAY")
            cs_staff = int(request.form.get("cs_staff", 1))
            cs_hours = float(request.form.get("cs_hours", 3.0))

            acc_calendar = request.form.get("acc_calendar", "HOLIDAYS_OFF")
            acc_staff = int(request.form.get("acc_staff", 1))
            acc_hours = float(request.form.get("acc_hours", 3.0))

            has_ndr = request.form.get("has_ndr")
            ndr_calendar = request.form.get("ndr_calendar", "SUNDAYS_HOLIDAYS_OFF")

            assigned_emp_id = request.form.get("assigned_employee_id")
            emp_ids = [int(assigned_emp_id)] if assigned_emp_id else []

            configs = [
                {"process_id": 1, "calendar": cs_calendar, "minimum_staff": cs_staff, "hours": cs_hours, "employee_ids": emp_ids},
                {"process_id": 2, "calendar": acc_calendar, "minimum_staff": acc_staff, "hours": acc_hours, "employee_ids": emp_ids},
            ]

            if has_ndr:
                configs.append({
                    "process_id": 3,
                    "calendar": ndr_calendar,
                    "minimum_staff": 1,
                    "hours": 2.0,
                    "employee_ids": emp_ids,
                })

            add_brand(name, configs)
            success = f"Successfully registered brand '{name}' with operational processes."
        except Exception as exc:
            error = str(exc)

    return render_template(
        "brands.html",
        requirements=get_brand_processes(),
        calendar_labels=calendar_labels,
        employees=get_employees(),
        processes=get_processes(),
        error=error,
        success=success,
    )


@app.route("/export/coverage.csv")
def export_coverage_csv():
    month = request.args.get("month", "")
    roster = load_monthly_roster(month) if month else None
    if not roster:
        return "No roster found for this month", 404

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Brand", "Process", "Required", "Assigned", "Status", "Assigned Staff IDs"])

    for date_key, day in sorted(roster["days"].items()):
        for cov in day["coverage"]:
            status = "Covered" if cov["assigned"] >= cov["required"] else "Gap"
            writer.writerow([
                date_key,
                cov["brand"],
                cov["process"],
                cov["required"],
                cov["assigned"],
                status,
                "; ".join(map(str, cov["employee_ids"]))
            ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=coverage-{month}.csv"}
    )


@app.route("/export/validation.csv")
def export_validation_csv():
    month = request.args.get("month", "")
    issues = validate_saved_roster(month) if month else None
    if issues is None:
        return "No validation issues to export", 404

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Severity", "Issue Type", "Detail", "Recommendation"])

    for issue in issues:
        writer.writerow([
            issue["date"],
            issue["severity"],
            issue["type"],
            issue["detail"],
            issue.get("recommendation", ""),
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=validation-issues-{month}.csv"}
    )


if __name__ == "__main__":
    app.run(debug=True, port=5000)
