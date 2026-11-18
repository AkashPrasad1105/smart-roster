import csv
import io
from datetime import date

from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    Response,
    jsonify,
    flash,
)

from roster_engine import (
    generate_monthly_roster,
    load_monthly_roster,
    get_month_dates,
    set_roster_status,
    recommend_backups,
    apply_backup_assignment,
)
from database import get_connection, get_employees, get_pods, get_single_employee


roster_bp = Blueprint("roster", __name__)


@roster_bp.route("/roster", methods=["GET", "POST"])
def monthly_roster():
    default_month = date.today().strftime("%Y-%m")

    if request.method == "POST":
        action = request.form.get("action", "generate")
        month = request.form.get("month", default_month)
        strategy = request.form.get("strategy", "CLIENT_PRIORITY")

        if action == "publish":
            set_roster_status(month, "PUBLISHED")
            return redirect(url_for("roster.monthly_roster", month=month, view=request.args.get("view", "company")))

        elif action == "unpublish":
            set_roster_status(month, "DRAFT")
            return redirect(url_for("roster.monthly_roster", month=month, view=request.args.get("view", "company")))

        else:
            generate_monthly_roster(month, strategy=strategy)
            return redirect(
                url_for("validation.validation_view", month=month)
            )

    month = request.args.get("month", default_month)
    view = request.args.get("view", "company")
    selected_pod = request.args.get("pod", "")
    selected_employee = request.args.get("employee", "")
    selected_brand = request.args.get("brand", "")
    selected_process = request.args.get("process", "")

    error = None
    saved_roster = None
    days = []
    all_employees = []
    visible_employees = []
    pods = []
    pod_data = {}
    brand_matrix = []
    process_matrix = []
    single_employee_schedule = None

    try:
        days = get_month_dates(month)
        saved_roster = load_monthly_roster(month)

        if saved_roster:
            all_employees = saved_roster["employees"]

            pods = sorted({
                employee["pod_name"]
                for employee in all_employees
            })

            # Filtered employees for company view
            visible_employees = [
                employee
                for employee in all_employees
                if (
                    not selected_pod
                    or employee["pod_name"] == selected_pod
                )
                and (
                    not selected_employee
                    or str(employee["id"]) == selected_employee
                )
            ]

            # 1. Pod-wise Data aggregation
            if view == "pod":
                db_pods = get_pods()
                pod_leads = {p["id"]: p["lead_name"] for p in db_pods}

                for p in pods:
                    pod_emps = [e for e in all_employees if e["pod_name"] == p]
                    pod_id = pod_emps[0]["pod_id"] if pod_emps else 0
                    total_hours = sum(
                        saved_roster["days"][day.isoformat()]["employees"][str(e["id"])]["hours"]
                        for day in days
                        for e in pod_emps
                        if str(e["id"]) in saved_roster["days"][day.isoformat()]["employees"]
                    )
                    pod_gaps = [
                        gap for gap in saved_roster["gaps"]
                        # Check if any assigned employee of this gap belongs to this pod
                    ]

                    pod_data[p] = {
                        "name": p,
                        "pod_id": pod_id,
                        "lead_name": pod_leads.get(pod_id, "None"),
                        "employees": pod_emps,
                        "total_hours": round(total_hours, 1),
                        "avg_hours": round(total_hours / len(pod_emps), 1) if pod_emps else 0,
                    }

            # 2. Brand-wise Matrix aggregation
            elif view == "brand":
                brands_dict = {}
                for req in saved_roster["requirements"]:
                    bname = req["brand_name"]
                    if bname not in brands_dict:
                        brands_dict[bname] = {
                            "brand_id": req["brand_id"],
                            "name": bname,
                            "requirements": [],
                        }
                    brands_dict[bname]["requirements"].append(req)

                for bname, binfo in sorted(brands_dict.items()):
                    if selected_brand and bname != selected_brand:
                        continue

                    day_statuses = {}
                    for day in days:
                        date_key = day.isoformat()
                        day_cov = {c["requirement_id"]: c for c in saved_roster["days"][date_key]["coverage"]}
                        open_reqs = [r for r in binfo["requirements"] if r["id"] in day_cov]

                        if not open_reqs:
                            day_statuses[date_key] = {"status": "Closed", "assigned": 0, "required": 0}
                        else:
                            has_gap = any(day_cov[r["id"]]["assigned"] < day_cov[r["id"]]["required"] for r in open_reqs)
                            tot_assigned = sum(day_cov[r["id"]]["assigned"] for r in open_reqs)
                            tot_required = sum(day_cov[r["id"]]["required"] for r in open_reqs)
                            day_statuses[date_key] = {
                                "status": "Gap" if has_gap else "Covered",
                                "assigned": tot_assigned,
                                "required": tot_required,
                                "details": [
                                    f"{r['process_name']}: {day_cov[r['id']]['assigned']}/{day_cov[r['id']]['required']}"
                                    for r in open_reqs
                                ]
                            }

                    brand_matrix.append({
                        "name": bname,
                        "requirements": binfo["requirements"],
                        "days": day_statuses,
                    })

            # 3. Process-wise Matrix aggregation
            elif view == "process":
                proc_dict = {}
                for req in saved_roster["requirements"]:
                    pname = req["process_name"]
                    if pname not in proc_dict:
                        proc_dict[pname] = []
                    proc_dict[pname].append(req)

                for pname, reqs in sorted(proc_dict.items()):
                    if selected_process and pname != selected_process:
                        continue

                    req_ids = {r["id"] for r in reqs}
                    day_proc = {}
                    for day in days:
                        date_key = day.isoformat()
                        covs = [c for c in saved_roster["days"][date_key]["coverage"] if c["requirement_id"] in req_ids]
                        tot_req = sum(c["required"] for c in covs)
                        tot_ass = sum(c["assigned"] for c in covs)
                        has_gap = any(c["assigned"] < c["required"] for c in covs)
                        day_proc[date_key] = {
                            "open": len(covs) > 0,
                            "required": tot_req,
                            "assigned": tot_ass,
                            "status": ("Gap" if has_gap else "Covered") if len(covs) > 0 else "Closed",
                        }

                    process_matrix.append({
                        "name": pname,
                        "brand_count": len(reqs),
                        "days": day_proc,
                    })

            # 4. Employee-wise Schedule
            elif view == "employee":
                emp_id_str = selected_employee or (str(all_employees[0]["id"]) if all_employees else "")
                emp_obj = next((e for e in all_employees if str(e["id"]) == emp_id_str), None)

                if emp_obj:
                    emp_days = []
                    total_work_days = 0
                    total_work_hours = 0.0
                    total_off_days = 0
                    total_leaves = 0

                    for day in days:
                        date_key = day.isoformat()
                        entry = saved_roster["days"][date_key]["employees"].get(emp_id_str, {
                            "status": "AVAILABLE",
                            "hours": 0.0,
                            "jobs": [],
                        })

                        status = entry["status"]
                        if status == "WORK":
                            total_work_days += 1
                            total_work_hours += entry["hours"]
                        elif status == "WEEKLY_OFF":
                            total_off_days += 1
                        elif status in ("LEAVE", "COMP_OFF", "UNAVAILABLE"):
                            total_leaves += 1

                        emp_days.append({
                            "date": day,
                            "date_key": date_key,
                            "entry": entry,
                        })

                    max_possible_hours = len(days) * emp_obj["daily_capacity_hours"]
                    utilization = round((total_work_hours / max_possible_hours) * 100, 1) if max_possible_hours else 0

                    single_employee_schedule = {
                        "employee": emp_obj,
                        "days": emp_days,
                        "total_work_days": total_work_days,
                        "total_work_hours": round(total_work_hours, 1),
                        "total_off_days": total_off_days,
                        "total_leaves": total_leaves,
                        "utilization": utilization,
                    }

    except ValueError as exc:
        error = str(exc)

    status_labels = {
        "WORK": "Work",
        "AVAILABLE": "Available",
        "WEEKLY_OFF": "Weekly off",
        "LEAVE": "Leave",
        "COMP_OFF": "Comp-off",
        "UNAVAILABLE": "Unavailable",
    }

    return render_template(
        "roster.html",
        month=month,
        view=view,
        days=days,
        roster=saved_roster,
        status_labels=status_labels,
        error=error,
        all_employees=all_employees,
        visible_employees=visible_employees,
        pods=pods,
        selected_pod=selected_pod,
        selected_employee=selected_employee,
        selected_brand=selected_brand,
        selected_process=selected_process,
        pod_data=pod_data,
        brand_matrix=brand_matrix,
        process_matrix=process_matrix,
        single_employee_schedule=single_employee_schedule,
    )


@roster_bp.route("/roster/export.csv")
def export_roster_csv():
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    roster = load_monthly_roster(month)

    if not roster:
        return "No roster found for this month", 404

    days = get_month_dates(month)
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    header = ["Employee ID", "Employee Name", "Pod"]
    for day in days:
        header.append(f"{day.strftime('%d %b')} Status")
        header.append(f"{day.strftime('%d %b')} Hours")
        header.append(f"{day.strftime('%d %b')} Jobs")
    writer.writerow(header)

    for emp in roster["employees"]:
        emp_key = str(emp["id"])
        row = [emp["id"], emp["name"], emp["pod_name"]]

        for day in days:
            date_key = day.isoformat()
            entry = roster["days"][date_key]["employees"].get(emp_key, {})
            status = entry.get("status", "AVAILABLE")
            hours = entry.get("hours", 0)
            jobs = "; ".join([f"{j['brand']} ({j['process']})" for j in entry.get("jobs", [])])
            row.extend([status, hours, jobs])

        writer.writerow(row)

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=roster-{month}.csv"}
    )


@roster_bp.route("/api/backup-recommendations")
def api_backup_recommendations():
    requirement_id = request.args.get("requirement_id", type=int)
    work_date = request.args.get("work_date", "")

    if not requirement_id or not work_date:
        return jsonify({"error": "Missing requirement_id or work_date"}), 400

    recs = recommend_backups(requirement_id, work_date)
    return jsonify({"recommendations": recs})


@roster_bp.route("/api/apply-backup", methods=["POST"])
def api_apply_backup():
    data = request.get_json() or request.form
    month = data.get("month")
    work_date = data.get("work_date")
    requirement_id = int(data.get("requirement_id"))
    employee_id = int(data.get("employee_id"))

    try:
        apply_backup_assignment(month, work_date, requirement_id, employee_id)
        return jsonify({"success": True, "message": "Backup employee assigned successfully."})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
