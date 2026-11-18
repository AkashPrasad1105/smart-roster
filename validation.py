from datetime import date
from flask import Blueprint, render_template, request, jsonify
from database import get_connection
from roster_engine import load_monthly_roster, get_month_dates, process_is_open
from staffing_rules import load_staffing_overrides, requirements_for_date

validation_bp = Blueprint("validation", __name__)


def validate_saved_roster(month):
    roster = load_monthly_roster(month)
    if roster is None:
        return None
    connection = get_connection()
    try:
        employees = {
            row["id"]: dict(row)
            for row in connection.execute("SELECT * FROM employees")
        }
        requirements = {
            row["id"]: dict(row)
            for row in connection.execute("""
                SELECT bp.*, brands.name AS brand_name,
                       processes.name AS process_name
                FROM brand_processes AS bp
                JOIN brands ON brands.id = bp.brand_id
                JOIN processes ON processes.id = bp.process_id
            """)
        }
        eligible = {
            (row["employee_id"], row["brand_process_id"])
            for row in connection.execute("SELECT * FROM assignments")
        }
        absences = {
            (row["employee_id"], row["absence_date"]): row["absence_type"]
            for row in connection.execute("SELECT * FROM absences")
        }
        holidays = {
            row["holiday_date"]
            for row in connection.execute("SELECT holiday_date FROM holidays")
        }
        overrides = load_staffing_overrides(connection)
    finally:
        connection.close()

    issues = []

    def add_issue(day, issue_type, detail, severity="CRITICAL", recommendation=""):
        issues.append({
            "date": day,
            "type": issue_type,
            "detail": detail,
            "severity": severity,
            "recommendation": recommendation,
        })

    for current_date in get_month_dates(month):
        day_text = current_date.isoformat()
        saved_day = roster["days"].get(day_text, {})
        entries = saved_day.get("employees", {})
        valid_staff = {requirement_id: set() for requirement_id in requirements}

        for employee_key, entry in entries.items():
            employee_id = int(employee_key)
            employee = employees.get(employee_id)
            jobs = entry.get("jobs", [])

            if employee is None:
                if jobs:
                    add_issue(day_text, "Unknown employee",
                              f"Employee {employee_id} no longer exists in database.",
                              severity="CRITICAL", recommendation="Remove this assignment from the roster.")
                continue

            if not jobs:
                if entry["status"] == "WORK":
                    add_issue(day_text, "Status inconsistency",
                              f"{employee['name']} is marked 'Work' but has 0 assigned responsibilities.",
                              severity="WARNING", recommendation="Assign a brand responsibility or switch status to 'Available'.")
                continue

            # Hard constraints check
            employee_has_critical_error = False

            if entry["status"] != "WORK":
                employee_has_critical_error = True
                add_issue(day_text, "Status conflict",
                          f"{employee['name']} has active responsibilities but status is set to {entry['status']}.",
                          severity="CRITICAL", recommendation="Change status to 'Work' or remove responsibilities.")

            absence = absences.get((employee_id, day_text))
            if absence:
                employee_has_critical_error = True
                add_issue(day_text, "Availability violation",
                          f"{employee['name']} has an approved absence ({absence}) but remains scheduled to work.",
                          severity="CRITICAL", recommendation="Assign a backup employee from the same pod.")

            hours = sum(
                requirements[job["requirement_id"]]["hours_per_employee"]
                for job in jobs if job["requirement_id"] in requirements
            )
            if hours > employee["daily_capacity_hours"]:
                employee_has_critical_error = True
                add_issue(day_text, "Capacity overload",
                          f"{employee['name']} is assigned {hours:.1f} hours, exceeding max daily capacity of {employee['daily_capacity_hours']} hours.",
                          severity="CRITICAL", recommendation="Reassign one or more tasks to balance daily load.")

            # Soft constraint check: Weekly off preference
            is_weekly_off = (current_date.weekday() == employee["weekly_off"])
            if is_weekly_off and not employee_has_critical_error:
                # Client coverage prioritized over preferred weekly off
                add_issue(day_text, "Weekly-off overridden",
                          f"{employee['name']} is scheduled on their preferred weekly off to satisfy mandatory client coverage.",
                          severity="WARNING", recommendation="Schedule a compensatory off day during the same month.")

            seen_requirements = set()
            for job in jobs:
                requirement_id = job["requirement_id"]
                requirement = requirements.get(requirement_id)
                if requirement_id in seen_requirements:
                    add_issue(day_text, "Duplicate assignment",
                              f"{employee['name']} is assigned to requirement #{requirement_id} multiple times on the same date.",
                              severity="WARNING", recommendation="Remove duplicate job entry.")
                    continue
                seen_requirements.add(requirement_id)

                if requirement is None:
                    add_issue(day_text, "Removed requirement",
                              f"{employee['name']} is assigned to a requirement that has been deleted.",
                              severity="CRITICAL", recommendation="Remove obsolete assignment.")
                    continue

                label = f"{requirement['brand_name']} — {requirement['process_name']}"

                # Closed calendar check
                if not process_is_open(requirement, current_date, holidays):
                    add_issue(day_text, "Calendar mismatch",
                              f"{employee['name']} is scheduled for {label}, which is closed on this date according to its operating calendar.",
                              severity="WARNING", recommendation="Unassign employee for this holiday/closed date.")
                    continue

                # Allocation eligibility
                if (employee_id, requirement_id) not in eligible:
                    if job.get("is_backup", False):
                        add_issue(day_text, "Backup coverage active",
                                  f"{employee['name']} is providing cross-coverage/backup for {label}.",
                                  severity="INFO", recommendation="Consider adding permanent eligibility if this allocation is regular.")
                    else:
                        add_issue(day_text, "Allocation notice",
                                  f"{employee['name']} is working {label} without formal assignment.",
                                  severity="WARNING", recommendation="Add employee to eligible brand assignment list.")

                # If no critical errors prevent employee from working, count towards coverage
                if not employee_has_critical_error:
                    valid_staff[requirement_id].add(employee_id)

        # Check coverage for all daily open requirements
        daily_requirements = requirements_for_date(
            requirements.values(), day_text, overrides
        )
        for requirement in daily_requirements:
            if not process_is_open(requirement, current_date, holidays):
                continue
            actual = len(valid_staff[requirement["id"]])
            required = requirement["minimum_staff"]
            if actual < required:
                deficit = required - actual
                add_issue(day_text, "Client coverage gap",
                          f"{requirement['brand_name']} — {requirement['process_name']}: "
                          f"Only {actual}/{required} staff assigned (shortage of {deficit}).",
                          severity="CRITICAL",
                          recommendation=f"Use the 'Find Backup' tool to assign an available pod teammate.")

    return issues


@validation_bp.route("/validation")
def validation_view():
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    filter_severity = request.args.get("severity", "")
    issues = None
    error = None

    try:
        all_issues = validate_saved_roster(month)
        if all_issues is not None:
            if filter_severity:
                issues = [i for i in all_issues if i["severity"] == filter_severity]
            else:
                issues = all_issues
        else:
            issues = None
    except ValueError:
        error = "Please select a valid month."

    critical_count = sum(1 for i in all_issues if i["severity"] == "CRITICAL") if all_issues else 0
    warning_count = sum(1 for i in all_issues if i["severity"] == "WARNING") if all_issues else 0
    info_count = sum(1 for i in all_issues if i["severity"] == "INFO") if all_issues else 0

    return render_template(
        "validation.html",
        month=month,
        issues=issues,
        all_issues=all_issues,
        error=error,
        filter_severity=filter_severity,
        critical_count=critical_count,
        warning_count=warning_count,
        info_count=info_count,
    )
