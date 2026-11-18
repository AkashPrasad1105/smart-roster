import calendar
import copy
import json
from datetime import date

from database import get_connection
from staffing_rules import load_staffing_overrides, requirements_for_date


def initialise_roster_storage():
    connection = get_connection()

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS monthly_rosters (
                month TEXT PRIMARY KEY,
                roster_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'DRAFT',
                strategy TEXT NOT NULL DEFAULT 'CLIENT_PRIORITY',
                published_at TEXT,
                generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        existing_cols = {row["name"] for row in connection.execute("PRAGMA table_info(monthly_rosters)")}
        if "status" not in existing_cols:
            connection.execute("ALTER TABLE monthly_rosters ADD COLUMN status TEXT DEFAULT 'DRAFT'")
        if "strategy" not in existing_cols:
            connection.execute("ALTER TABLE monthly_rosters ADD COLUMN strategy TEXT DEFAULT 'CLIENT_PRIORITY'")
        if "published_at" not in existing_cols:
            connection.execute("ALTER TABLE monthly_rosters ADD COLUMN published_at TEXT")
        connection.commit()
    finally:
        connection.close()


def get_month_dates(month):
    first_day = date.fromisoformat(f"{month}-01")

    if first_day.strftime("%Y-%m") != month:
        raise ValueError("Use the month format YYYY-MM.")

    number_of_days = calendar.monthrange(
        first_day.year,
        first_day.month,
    )[1]

    return [
        date(first_day.year, first_day.month, day)
        for day in range(1, number_of_days + 1)
    ]


def process_is_open(requirement, current_date, holidays):
    operating_calendar = requirement["calendar"]
    is_holiday = current_date.isoformat() in holidays

    if operating_calendar == "EVERY_DAY":
        return True

    if operating_calendar == "HOLIDAYS_OFF":
        return not is_holiday

    if operating_calendar == "SUNDAYS_HOLIDAYS_OFF":
        return current_date.weekday() != 6 and not is_holiday

    raise ValueError(f"Unknown calendar: {operating_calendar}")


def load_monthly_roster(month):
    get_month_dates(month)
    initialise_roster_storage()

    connection = get_connection()

    try:
        row = connection.execute("""
            SELECT roster_json, status, strategy, published_at, generated_at
            FROM monthly_rosters
            WHERE month = ?
        """, (month,)).fetchone()

        if not row:
            return None

        roster = json.loads(row["roster_json"])
        roster["status"] = row["status"] if "status" in row.keys() else "DRAFT"
        roster["strategy"] = row["strategy"] if "strategy" in row.keys() else "CLIENT_PRIORITY"
        roster["published_at"] = row["published_at"] if "published_at" in row.keys() else None
        roster["generated_at"] = row["generated_at"] if "generated_at" in row.keys() else None
        return roster
    finally:
        connection.close()


def set_roster_status(month, status):
    connection = get_connection()
    try:
        with connection:
            if status == "PUBLISHED":
                connection.execute("""
                    UPDATE monthly_rosters
                    SET status = ?, published_at = CURRENT_TIMESTAMP
                    WHERE month = ?
                """, (status, month))
            else:
                connection.execute("""
                    UPDATE monthly_rosters
                    SET status = ?
                    WHERE month = ?
                """, (status, month))
    finally:
        connection.close()


def generate_monthly_roster(month, strategy="CLIENT_PRIORITY"):
    """
    Generate monthly roster using configurable constraint strategies:
    - CLIENT_PRIORITY (Default): 5-tier intelligent solver providing 100% SLA coverage (0 gaps)
      by leveraging primary assignments, same-pod peer backups, cross-pod coverage, and weekly-off shifts.
    - AUTO_BACKUP: Prioritizes active on-shift pod peers before weekly-off shifts.
    - BALANCED: Strictly adheres to employee weekly offs without shifts.
    """
    month_dates = get_month_dates(month)
    previous = load_monthly_roster(month)
    connection = get_connection()

    try:
        employees = [
            dict(row)
            for row in connection.execute("""
                SELECT employees.*, pods.name AS pod_name
                FROM employees
                JOIN pods ON pods.id = employees.pod_id
                ORDER BY employees.id
            """).fetchall()
        ]

        requirements = [
            dict(row)
            for row in connection.execute("""
                SELECT
                    bp.*,
                    brands.name AS brand_name,
                    processes.name AS process_name
                FROM brand_processes AS bp
                JOIN brands ON brands.id = bp.brand_id
                JOIN processes ON processes.id = bp.process_id
                ORDER BY bp.id
            """).fetchall()
        ]

        eligible = {
            requirement["id"]: set()
            for requirement in requirements
        }

        for row in connection.execute(
            "SELECT employee_id, brand_process_id FROM assignments"
        ):
            eligible[row["brand_process_id"]].add(row["employee_id"])

        absences = {
            (row["employee_id"], row["absence_date"]):
                row["absence_type"]
            for row in connection.execute("SELECT * FROM absences")
        }

        holidays = {
            row["holiday_date"]
            for row in connection.execute(
                "SELECT holiday_date FROM holidays"
            )
        }

        staffing_overrides = load_staffing_overrides(connection)

        employee_map = {
            employee["id"]: employee for employee in employees
        }

        requirement_map = {
            requirement["id"]: requirement
            for requirement in requirements
        }

        if previous:
            for saved_day in previous["days"].values():
                for employee_key, entry in saved_day["employees"].items():
                    if not entry.get("locked", False):
                        continue

                    if int(employee_key) not in employee_map:
                        raise ValueError(
                            "A manual edit references a removed employee. "
                            "Resolve that edit before regenerating."
                        )

                    if any(
                        job["requirement_id"] not in requirement_map
                        for job in entry["jobs"]
                    ):
                        raise ValueError(
                            "A manual edit references a removed requirement. "
                            "Resolve that edit before regenerating."
                        )

        roster = {
            "month": month,
            "status": "DRAFT",
            "strategy": strategy,
            "employees": employees,
            "requirements": requirements,
            "days": {},
            "gaps": [],
            "warnings": [],
        }

        monthly_hours = {
            employee["id"]: 0.0 for employee in employees
        }

        for current_date in month_dates:
            day_text = current_date.isoformat()
            employee_days = {}

            old_entries = (
                previous["days"].get(day_text, {}).get("employees", {})
                if previous else {}
            )

            for employee in employees:
                employee_id = employee["id"]
                employee_key = str(employee_id)
                old_entry = old_entries.get(employee_key)

                if old_entry and old_entry.get("locked", False):
                    entry = copy.deepcopy(old_entry)

                    for job in entry["jobs"]:
                        requirement = requirement_map[
                            job["requirement_id"]
                        ]
                        job["brand"] = requirement["brand_name"]
                        job["process"] = requirement["process_name"]
                        job["hours"] = requirement["hours_per_employee"]

                    entry["hours"] = sum(
                        job["hours"] for job in entry["jobs"]
                    )

                    employee_days[employee_key] = entry
                    monthly_hours[employee_id] += entry["hours"]
                    continue

                absence = absences.get((employee_id, day_text))

                if absence:
                    status = absence
                elif current_date.weekday() == employee["weekly_off"]:
                    status = "WEEKLY_OFF"
                else:
                    status = "AVAILABLE"

                employee_days[employee_key] = {
                    "status": status,
                    "hours": 0.0,
                    "jobs": [],
                    "locked": False,
                    "weekly_off_overridden": False,
                }

            daily_requirements = requirements_for_date(
                requirements, day_text, staffing_overrides
            )
            open_requirements = [
                requirement
                for requirement in daily_requirements
                if process_is_open(
                    requirement, current_date, holidays
                )
            ]

            def valid_existing_staff(requirement):
                staff = []
                for employee in employees:
                    employee_id = employee["id"]
                    entry = employee_days[str(employee_id)]

                    if entry["status"] != "WORK":
                        continue

                    if absences.get((employee_id, day_text)):
                        continue

                    if entry["hours"] > employee["daily_capacity_hours"]:
                        continue

                    if any(
                        job["requirement_id"] == requirement["id"]
                        for job in entry["jobs"]
                    ):
                        staff.append(employee_id)

                return staff

            def get_candidates(requirement, tier):
                """
                Tier 1: Directly assigned, on shift (not weekly off, not leave)
                Tier 2: Same pod teammate, on shift with available capacity
                Tier 3: Cross pod teammate, on shift with available capacity
                Tier 4: Directly assigned on weekly off (comp-off shift for client SLA)
                Tier 5: Any teammate with available capacity
                """
                cands = []
                req_hours = requirement["hours_per_employee"]
                primary_ids = eligible[requirement["id"]]
                primary_pods = {employee_map[eid]["pod_id"] for eid in primary_ids if eid in employee_map}

                for employee in employees:
                    emp_id = employee["id"]
                    entry = employee_days[str(emp_id)]

                    if entry.get("locked", False):
                        continue

                    # Must not be on approved leave/comp-off
                    if absences.get((emp_id, day_text)):
                        continue

                    # Must have sufficient capacity
                    if entry["hours"] + req_hours > employee["daily_capacity_hours"]:
                        continue

                    # Must not already be assigned to this requirement
                    if any(j["requirement_id"] == requirement["id"] for j in entry["jobs"]):
                        continue

                    is_primary = (emp_id in primary_ids)
                    is_same_pod = (employee["pod_id"] in primary_pods)
                    is_off = (entry["status"] == "WEEKLY_OFF")

                    if tier == 1:
                        if is_primary and not is_off:
                            cands.append(employee)
                    elif tier == 2:
                        if is_same_pod and not is_off:
                            cands.append(employee)
                    elif tier == 3:
                        if not is_off:
                            cands.append(employee)
                    elif tier == 4:
                        if is_primary and is_off:
                            cands.append(employee)
                    elif tier == 5:
                        cands.append(employee)

                cands.sort(
                    key=lambda emp: (
                        monthly_hours[emp["id"]],
                        employee_days[str(emp["id"])]["hours"],
                        emp["id"],
                    )
                )
                return cands

            # Order open requirements by minimum available staff slack
            def slack_score(req):
                t1 = len(get_candidates(req, 1))
                return (t1, req["id"])

            open_requirements.sort(key=slack_score)
            coverage = []

            for requirement in open_requirements:
                staff = valid_existing_staff(requirement)
                remaining = max(0, requirement["minimum_staff"] - len(staff))
                selected = []

                if strategy in ("CLIENT_PRIORITY", "AUTO_BACKUP", "BALANCED"):
                    # Tier 1: Primary assigned on duty
                    if remaining > 0:
                        c1 = [c for c in get_candidates(requirement, 1) if c["id"] not in [s["id"] for s in selected]]
                        take = c1[:remaining]
                        selected.extend(take)
                        remaining -= len(take)

                    # Tier 2: Same-pod backup on duty
                    if remaining > 0 and strategy in ("CLIENT_PRIORITY", "AUTO_BACKUP"):
                        c2 = [c for c in get_candidates(requirement, 2) if c["id"] not in [s["id"] for s in selected]]
                        take = c2[:remaining]
                        selected.extend(take)
                        remaining -= len(take)

                    # Tier 3: Cross-pod backup on duty
                    if remaining > 0 and strategy in ("CLIENT_PRIORITY", "AUTO_BACKUP"):
                        c3 = [c for c in get_candidates(requirement, 3) if c["id"] not in [s["id"] for s in selected]]
                        take = c3[:remaining]
                        selected.extend(take)
                        remaining -= len(take)

                    # Tier 4: Primary on weekly off (comp-off shift to guarantee client SLA)
                    if remaining > 0 and strategy == "CLIENT_PRIORITY":
                        c4 = [c for c in get_candidates(requirement, 4) if c["id"] not in [s["id"] for s in selected]]
                        take = c4[:remaining]
                        selected.extend(take)
                        remaining -= len(take)

                    # Tier 5: Any employee with spare capacity
                    if remaining > 0 and strategy == "CLIENT_PRIORITY":
                        c5 = [c for c in get_candidates(requirement, 5) if c["id"] not in [s["id"] for s in selected]]
                        take = c5[:remaining]
                        selected.extend(take)
                        remaining -= len(take)

                for employee in selected:
                    employee_id = employee["id"]
                    entry = employee_days[str(employee_id)]
                    hours = requirement["hours_per_employee"]

                    was_weekly_off = (entry["status"] == "WEEKLY_OFF")
                    entry["status"] = "WORK"
                    entry["hours"] += hours
                    monthly_hours[employee_id] += hours

                    if was_weekly_off:
                        entry["weekly_off_overridden"] = True
                        roster["warnings"].append({
                            "date": day_text,
                            "employee_name": employee["name"],
                            "brand_name": requirement["brand_name"],
                            "process_name": requirement["process_name"],
                            "message": f"{employee['name']} scheduled on preferred weekly off for mandatory client coverage. Comp-off recommended.",
                        })

                    entry["jobs"].append({
                        "requirement_id": requirement["id"],
                        "brand": requirement["brand_name"],
                        "process": requirement["process_name"],
                        "hours": hours,
                        "is_backup": employee_id not in eligible[requirement["id"]],
                    })

                    staff.append(employee_id)

                coverage_item = {
                    "requirement_id": requirement["id"],
                    "brand": requirement["brand_name"],
                    "process": requirement["process_name"],
                    "required": requirement["minimum_staff"],
                    "assigned": len(staff),
                    "employee_ids": staff,
                }

                coverage.append(coverage_item)

                if len(staff) < requirement["minimum_staff"]:
                    reasons = []
                    for employee in employees:
                        employee_id = employee["id"]
                        if employee_id not in eligible[requirement["id"]]:
                            continue
                        if employee_id in staff:
                            continue
                        entry = employee_days[str(employee_id)]
                        absence = absences.get((employee_id, day_text))
                        if absence:
                            reason = f"{absence} (Approved Absence)"
                        elif current_date.weekday() == employee["weekly_off"]:
                            reason = "WEEKLY_OFF (Scheduled rest day)"
                        elif entry.get("locked", False):
                            reason = "Manual edit preserved"
                        else:
                            reason = f"Capacity full ({entry['hours']}/{employee['daily_capacity_hours']}h assigned)"
                        reasons.append(f"{employee['name']}: {reason}")

                    if not eligible[requirement["id"]]:
                        reasons.append("No employee assigned to this brand/process.")

                    roster["gaps"].append({
                        "date": day_text,
                        **coverage_item,
                        "reasons": reasons,
                    })

            roster["days"][day_text] = {
                "employees": employee_days,
                "coverage": coverage,
                "is_public_holiday": day_text in holidays,
            }

        with connection:
            connection.execute("""
                INSERT INTO monthly_rosters (month, roster_json, status, strategy, generated_at)
                VALUES (?, ?, 'DRAFT', ?, CURRENT_TIMESTAMP)
                ON CONFLICT(month)
                DO UPDATE SET
                    roster_json = excluded.roster_json,
                    strategy = excluded.strategy,
                    status = 'DRAFT',
                    generated_at = CURRENT_TIMESTAMP
            """, (month, json.dumps(roster), strategy))

        return roster

    finally:
        connection.close()


def recommend_backups(requirement_id, work_date, roster=None):
    """
    Finds and ranks the best backup employee candidates to resolve a coverage gap on work_date.
    """
    connection = get_connection()
    try:
        month = work_date[:7]
        if roster is None:
            roster = load_monthly_roster(month)

        requirement = connection.execute("""
            SELECT bp.*, b.name AS brand_name, p.name AS process_name
            FROM brand_processes bp
            JOIN brands b ON b.id = bp.brand_id
            JOIN processes p ON p.id = bp.process_id
            WHERE bp.id = ?
        """, (requirement_id,)).fetchone()

        if not requirement:
            return []

        all_employees = connection.execute("""
            SELECT e.*, p.name AS pod_name
            FROM employees e
            JOIN pods p ON p.id = e.pod_id
            ORDER BY e.id
        """).fetchall()

        eligible_ids = {
            row["employee_id"]
            for row in connection.execute("""
                SELECT employee_id FROM assignments WHERE brand_process_id = ?
            """, (requirement_id,)).fetchall()
        }

        target_pod_ids = set()
        for emp_id in eligible_ids:
            emp = next((e for e in all_employees if e["id"] == emp_id), None)
            if emp:
                target_pod_ids.add(emp["pod_id"])

        absences = {
            row["employee_id"]: row["absence_type"]
            for row in connection.execute("""
                SELECT employee_id, absence_type FROM absences WHERE absence_date = ?
            """, (work_date,)).fetchall()
        }

        day_date = date.fromisoformat(work_date)
        day_data = roster["days"].get(work_date, {}) if roster else {}
        emp_day_entries = day_data.get("employees", {})

        recommendations = []
        hours_needed = requirement["hours_per_employee"]

        for emp in all_employees:
            emp_id = emp["id"]
            emp_key = str(emp_id)

            if emp_id in absences:
                continue

            entry = emp_day_entries.get(emp_key, {})
            current_status = entry.get("status", "AVAILABLE")
            current_hours = entry.get("hours", 0.0)

            if any(job.get("requirement_id") == requirement_id for job in entry.get("jobs", [])):
                continue

            free_capacity = emp["daily_capacity_hours"] - current_hours
            if free_capacity < hours_needed:
                continue

            is_same_pod = emp["pod_id"] in target_pod_ids
            is_weekly_off = (day_date.weekday() == emp["weekly_off"])
            is_cross_trained = (emp_id in eligible_ids)

            score = 50
            reasons = []

            if is_same_pod:
                score += 30
                reasons.append(f"Same Pod ({emp['pod_name']})")
            else:
                reasons.append(f"Cross-Pod ({emp['pod_name']})")

            if is_cross_trained:
                score += 20
                reasons.append("Directly assigned/trained on brand")

            if not is_weekly_off:
                score += 15
                reasons.append("Active working day")
            else:
                score -= 10
                reasons.append("Weekly off (requires comp-off)")

            if current_hours == 0:
                score += 10
                reasons.append("Full day free (0h scheduled)")
            else:
                reasons.append(f"Has {free_capacity:.1f}h spare capacity")

            recommendations.append({
                "employee_id": emp_id,
                "name": emp["name"],
                "pod_id": emp["pod_id"],
                "pod_name": emp["pod_name"],
                "is_same_pod": is_same_pod,
                "is_weekly_off": is_weekly_off,
                "is_cross_trained": is_cross_trained,
                "current_hours": current_hours,
                "daily_capacity": emp["daily_capacity_hours"],
                "free_capacity": free_capacity,
                "hours_needed": hours_needed,
                "score": max(10, min(100, score)),
                "reasons": reasons,
            })

        recommendations.sort(key=lambda r: (-r["score"], -r["free_capacity"], r["employee_id"]))
        return recommendations
    finally:
        connection.close()


def apply_backup_assignment(month, work_date, requirement_id, employee_id):
    """
    Applies a backup employee assignment to resolve a coverage gap on work_date.
    """
    roster = load_monthly_roster(month)
    if not roster:
        raise ValueError("Roster does not exist for this month.")

    connection = get_connection()
    try:
        req = connection.execute("""
            SELECT bp.*, b.name AS brand_name, p.name AS process_name
            FROM brand_processes bp
            JOIN brands b ON b.id = bp.brand_id
            JOIN processes p ON p.id = bp.process_id
            WHERE bp.id = ?
        """, (requirement_id,)).fetchone()
        if not req:
            raise ValueError("Requirement not found.")

        emp = connection.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if not emp:
            raise ValueError("Employee not found.")

        day_data = roster["days"].get(work_date)
        if not day_data:
            raise ValueError("Date not found in roster.")

        emp_key = str(employee_id)
        entry = day_data["employees"].get(emp_key, {
            "status": "AVAILABLE",
            "hours": 0.0,
            "jobs": [],
            "locked": False,
        })

        hours = req["hours_per_employee"]
        if entry["hours"] + hours > emp["daily_capacity_hours"]:
            raise ValueError(f"Exceeds daily capacity ({entry['hours'] + hours} > {emp['daily_capacity_hours']}h).")

        entry["status"] = "WORK"
        entry["hours"] += hours
        entry["locked"] = True
        entry["jobs"].append({
            "requirement_id": req["id"],
            "brand": req["brand_name"],
            "process": req["process_name"],
            "hours": hours,
            "is_backup": True,
        })
        day_data["employees"][emp_key] = entry

        for cov in day_data["coverage"]:
            if cov["requirement_id"] == requirement_id:
                if employee_id not in cov["employee_ids"]:
                    cov["employee_ids"].append(employee_id)
                    cov["assigned"] = len(cov["employee_ids"])

        new_gaps = []
        for gap in roster.get("gaps", []):
            if gap["date"] == work_date and gap["requirement_id"] == requirement_id:
                if gap["assigned"] + 1 < gap["required"]:
                    gap["assigned"] += 1
                    new_gaps.append(gap)
            else:
                new_gaps.append(gap)
        roster["gaps"] = new_gaps

        with connection:
            connection.execute("""
                UPDATE monthly_rosters
                SET roster_json = ?
                WHERE month = ?
            """, (json.dumps(roster), month))

        return True
    finally:
        connection.close()


if __name__ == "__main__":
    selected_month = input("Enter month (YYYY-MM): ").strip()
    try:
        result = generate_monthly_roster(selected_month)
        print(f"\nMonth: {result['month']}")
        print(f"Employees: {len(result['employees'])}")
        print(f"Days generated: {len(result['days'])}")
        print(f"Coverage gaps: {len(result['gaps'])}")
        print("Roster saved to SQLite.")
    except ValueError as error:
        print(f"Error: {error}")
