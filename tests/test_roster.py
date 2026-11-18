import unittest
from datetime import date
from database import (
    initialise_database,
    initialise_brand_data,
    get_employees,
    get_brand_processes,
    get_pods,
    get_connection,
)
from availability import initialise_availability
from roster_engine import (
    initialise_roster_storage,
    generate_monthly_roster,
    load_monthly_roster,
    recommend_backups,
    apply_backup_assignment,
)
from validation import validate_saved_roster


class TestSmartRosterSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        initialise_database()
        initialise_brand_data()
        initialise_availability()
        initialise_roster_storage()

    def test_01_team_and_brand_seed_data(self):
        """Verify >=30 employees, 3 pods with leads, and 25 brands with 80/20 distribution."""
        employees = get_employees()
        self.assertGreaterEqual(len(employees), 30)

        pods = get_pods()
        self.assertEqual(len(pods), 3)
        for pod in pods:
            self.assertIsNotNone(pod["lead_name"])

        requirements = get_brand_processes()
        brands = {r["brand_name"] for r in requirements}
        self.assertEqual(len(brands), 25)

        # Verify 80% (20/25) single-resource brands vs 20% (5/25) multi-resource brands
        conn = get_connection()
        single_res_count = 0
        multi_res_count = 0
        for b_id in range(1, 26):
            assigned_emps = conn.execute("""
                SELECT COUNT(DISTINCT employee_id) FROM assignments a
                JOIN brand_processes bp ON bp.id = a.brand_process_id
                WHERE bp.brand_id = ?
            """, (b_id,)).fetchone()[0]
            if assigned_emps == 1:
                single_res_count += 1
            else:
                multi_res_count += 1
        conn.close()

        self.assertEqual(single_res_count, 20)
        self.assertEqual(multi_res_count, 5)

    def test_02_roster_generation_strategies(self):
        """Verify BALANCED vs CLIENT_PRIORITY strategies."""
        test_month = "2026-11"

        # Balanced: strictly respects weekly off; gaps flagged for single-resource brands
        roster_balanced = generate_monthly_roster(test_month, strategy="BALANCED")
        self.assertEqual(roster_balanced["month"], test_month)
        self.assertGreater(len(roster_balanced["gaps"]), 0)

        # Client-Priority: covers mandatory 365d operations; gaps eliminated or minimized
        roster_priority = generate_monthly_roster(test_month, strategy="CLIENT_PRIORITY")
        self.assertEqual(len(roster_priority["gaps"]), 0)
        self.assertGreater(len(roster_priority["warnings"]), 0)

    def test_03_capacity_constraints(self):
        """Ensure no employee is assigned beyond daily capacity."""
        test_month = "2026-11"
        roster = load_monthly_roster(test_month)
        emp_capacities = {e["id"]: e["daily_capacity_hours"] for e in roster["employees"]}

        for day_text, day in roster["days"].items():
            for emp_key, entry in day["employees"].items():
                emp_id = int(emp_key)
                cap = emp_capacities[emp_id]
                self.assertLessEqual(entry["hours"], cap, f"Employee {emp_id} exceeded capacity on {day_text}")

    def test_04_validation_engine(self):
        """Ensure live validation correctly separates critical errors from warnings."""
        test_month = "2026-11"
        issues = validate_saved_roster(test_month)
        self.assertIsNotNone(issues)

        # Under CLIENT_PRIORITY, zero critical outages
        critical = [i for i in issues if i["severity"] == "CRITICAL"]
        self.assertEqual(len(critical), 0)

        # Weekly off overrides are flagged as warnings
        warnings = [i for i in issues if i["severity"] == "WARNING"]
        self.assertGreater(len(warnings), 0)

    def test_05_backup_recommendation_engine(self):
        """Verify recommendation algorithm finds same-pod available candidates."""
        recs = recommend_backups(1, "2026-11-02")
        self.assertGreater(len(recs), 0)

        # Highest scored candidate should belong to the same pod
        top_rec = recs[0]
        self.assertEqual(top_rec["pod_name"], "North Pod")
        self.assertGreaterEqual(top_rec["score"], 80)
        self.assertGreaterEqual(top_rec["free_capacity"], top_rec["hours_needed"])

    def test_06_apply_backup_resolution(self):
        """Verify 1-click backup assignment updates the saved roster and coverage."""
        test_month = "2026-11"
        work_date = "2026-11-02"
        # Generate in BALANCED mode so gaps exist
        generate_monthly_roster(test_month, strategy="BALANCED")
        roster = load_monthly_roster(test_month)

        req_id = 1
        day_cov = next(c for c in roster["days"][work_date]["coverage"] if c["requirement_id"] == req_id)
        initial_assigned = day_cov["assigned"]

        # Find and apply backup
        recs = recommend_backups(req_id, work_date, roster)
        backup_emp_id = recs[0]["employee_id"]

        success = apply_backup_assignment(test_month, work_date, req_id, backup_emp_id)
        self.assertTrue(success)

        # Reload and verify assignment
        updated_roster = load_monthly_roster(test_month)
        updated_cov = next(c for c in updated_roster["days"][work_date]["coverage"] if c["requirement_id"] == req_id)
        self.assertIn(backup_emp_id, updated_cov["employee_ids"])
        self.assertEqual(updated_cov["assigned"], initial_assigned + 1)

    @classmethod
    def tearDownClass(cls):
        # Restore November 2026 to pristine 100% SLA covered state with 0 critical violations
        generate_monthly_roster("2026-11", strategy="CLIENT_PRIORITY")


if __name__ == "__main__":
    unittest.main()
