from datetime import date
from flask import Blueprint, render_template, request, redirect, url_for, flash
from database import get_connection, get_employees, get_brand_processes
from roster_engine import load_monthly_roster, recommend_backups, apply_backup_assignment

simulator_bp = Blueprint("simulator", __name__)


@simulator_bp.route("/simulator", methods=["GET", "POST"])
def change_simulator():
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    scenario = request.args.get("scenario", "single_resource")

    simulation_result = None
    roster = load_monthly_roster(month)
    employees = get_employees()
    requirements = get_brand_processes()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "simulate_single_resource":
            employee_id = int(request.form.get("employee_id", 1))
            work_date = request.form.get("work_date", f"{month}-15")
            month = work_date[:7]
            roster = load_monthly_roster(month)

            if not roster:
                simulation_result = {
                    "error": f"Please generate a roster for {month} first."
                }
            else:
                emp = next((e for e in roster["employees"] if e["id"] == employee_id), None)
                day_data = roster["days"].get(work_date)

                if not day_data:
                    simulation_result = {"error": f"Date {work_date} is outside the generated roster month."}
                else:
                    emp_entry = day_data["employees"].get(str(employee_id), {})
                    assigned_jobs = emp_entry.get("jobs", [])

                    # Identify affected requirements
                    affected_reqs = []
                    for job in assigned_jobs:
                        req_id = job["requirement_id"]
                        req = next((r for r in roster["requirements"] if r["id"] == req_id), None)
                        backups = recommend_backups(req_id, work_date, roster)
                        affected_reqs.append({
                            "requirement_id": req_id,
                            "brand": job["brand"],
                            "process": job["process"],
                            "hours": job["hours"],
                            "recommendations": backups[:3],
                        })

                    simulation_result = {
                        "type": "single_resource",
                        "employee": emp,
                        "date": work_date,
                        "status_before": emp_entry.get("status", "AVAILABLE"),
                        "hours_before": emp_entry.get("hours", 0.0),
                        "jobs_count": len(assigned_jobs),
                        "affected_requirements": affected_reqs,
                        "has_impact": len(affected_reqs) > 0,
                    }

        elif action == "simulate_multi_resource":
            # Multi-resource brand test (e.g. Brand 25 Green Pantry)
            brand_id = int(request.form.get("brand_id", 25))
            unavailable_emp_id = int(request.form.get("unavailable_emp_id", 29))
            work_date = request.form.get("work_date", f"{month}-15")
            month = work_date[:7]
            roster = load_monthly_roster(month)

            if not roster:
                simulation_result = {"error": f"Please generate a roster for {month} first."}
            else:
                day_data = roster["days"].get(work_date)
                connection = get_connection()
                try:
                    brand = connection.execute("SELECT * FROM brands WHERE id = ?", (brand_id,)).fetchone()
                    brand_reqs = connection.execute("""
                        SELECT bp.*, p.name AS process_name
                        FROM brand_processes bp
                        JOIN processes p ON p.id = bp.process_id
                        WHERE bp.brand_id = ?
                    """, (brand_id,)).fetchall()

                    # Find assigned employees for this brand
                    assigned_emps = connection.execute("""
                        SELECT DISTINCT e.*
                        FROM employees e
                        JOIN assignments a ON a.employee_id = e.id
                        JOIN brand_processes bp ON bp.id = a.brand_process_id
                        WHERE bp.brand_id = ?
                    """, (brand_id,)).fetchall()

                    remaining_emps = [e for e in assigned_emps if e["id"] != unavailable_emp_id]
                    unavail_emp = next((e for e in assigned_emps if e["id"] == unavailable_emp_id), None)

                    # Evaluate if remaining staff can satisfy total required capacity
                    total_required_hours = sum(r["minimum_staff"] * r["hours_per_employee"] for r in brand_reqs)
                    remaining_capacity = sum(e["daily_capacity_hours"] for e in remaining_emps)
                    can_cover = remaining_capacity >= total_required_hours

                    process_breakdown = []
                    for r in brand_reqs:
                        # Check eligible remaining
                        eligible_remaining = connection.execute("""
                            SELECT e.name FROM employees e
                            JOIN assignments a ON a.employee_id = e.id
                            WHERE a.brand_process_id = ? AND e.id != ?
                        """, (r["id"], unavailable_emp_id)).fetchall()

                        coverage_status = "Covered" if len(eligible_remaining) >= r["minimum_staff"] else "Coverage Deficit"
                        process_breakdown.append({
                            "process_name": r["process_name"],
                            "required_staff": r["minimum_staff"],
                            "eligible_remaining": [e["name"] for e in eligible_remaining],
                            "status": coverage_status,
                        })

                    simulation_result = {
                        "type": "multi_resource",
                        "brand": brand,
                        "date": work_date,
                        "unavailable_emp": unavail_emp,
                        "remaining_emps": remaining_emps,
                        "total_required_hours": total_required_hours,
                        "remaining_capacity": remaining_capacity,
                        "can_cover": can_cover,
                        "process_breakdown": process_breakdown,
                    }
                finally:
                    connection.close()

    return render_template(
        "simulator.html",
        month=month,
        scenario=scenario,
        simulation_result=simulation_result,
        employees=employees,
        requirements=requirements,
    )
