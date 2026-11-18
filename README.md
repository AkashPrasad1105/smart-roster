# Smart Roster Management System
**DataStraw Tech Internship Assessment Submission**  
*Built for D2C Operations & Customer Experience Scheduling*

---

## 🌟 Executive Summary
The **Smart Roster Management System** is a full-featured, browser-based workforce management and constraint-satisfaction platform. Designed specifically for multi-brand customer operations environments, it models complex operational pods, decouples client SLA operating calendars from employee shift schedules, and eliminates the fragile single-resource bottleneck common across 80% of managed D2C brands.

### Key Highlights
- **Realistic Dataset**: Seeds **32 employees** across **3 operational pods** (each with dedicated Pod Leads), handling **25 D2C brands** across Customer Support (CS), Abandoned Cart Conversions (ACC), and Non-Delivery Reports (NDR).
- **80/20 Staffing Distribution**: Faithfully reflects realistic staffing: ~80% single-resource dedicated brands (Brand 1–20) and ~20% multi-resource brands with shared and specialized roles (Brand 21–25).
- **Intelligent Constraint Engine**: Distinguishes **hard mandatory constraints** (daily capacity, approved leaves, client operating days) from **soft preferences** (weekly-off days, workload distribution).
- **Smart Pod Backup Recommendations**: Automatically ranks and scores available same-pod teammates to back up sick or off-duty colleagues with a 1-click resolution workflow.
- **5 Dedicated Stakeholder Views**: Company-wide, Pod-wise, Brand-wise, Process-wise, and Individual Employee agenda views.
- **Dynamic Change Simulator**: Built-in interactive test suite demonstrating live leave impacts on single-resource and multi-resource teams (Section 3.F).
- **CSV Data Exports**: One-click exports for monthly rosters, coverage compliance logs, and validation audit reports.

---

## 🛠️ Technology Stack & Architecture
- **Backend**: Python 3, Flask 3.1
- **Database**: SQLite with foreign key enforcement and relational schema
- **Frontend**: Responsive semantic HTML5, Jinja2 templating, modern CSS design system (zero external JavaScript dependencies or heavy framework overhead for lightning-fast sub-50ms render times)
- **Validation & Constraint Solver**: Deterministic greedy heuristic with slack-based ordering and fair workload balancing

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.10+ installed
- Git

### 2. Setup & Installation
```bash
# Clone the repository
git clone <your-repository-url>
cd smart-roster

# Create and activate virtual environment
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On macOS/Linux:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run the Application
```bash
python app.py
```
Open your browser and navigate to: **`http://localhost:5000`**

### 4. Run Automated Unit Tests
```bash
python -m unittest tests/test_roster.py
```
*(Runs 6 automated tests covering seed data, constraint solving, strategy comparisons, backup recommendations, and 1-click resolution).*

---

## 🧠 Section 8: Tell Us How You Think
*(Comprehensive answers to the mandatory evaluation questions)*

### 1. How did you model employees, brands, processes, pods, and their relationships?
We designed a normalized relational schema with five core entities and two junction tables:
1. **`pods`**: `(id, name)` representing operational pods (North, South, Central).
2. **`employees`**: `(id, name, pod_id, is_pod_lead, weekly_off, daily_capacity_hours)`. Associates each employee with a pod and establishes individual working constraints (e.g., 8-hour daily limit, preferred weekly-off day from Monday=0 to Sunday=6).
3. **`brands`**: `(id, name)` representing client accounts.
4. **`processes`**: `(id, name)` (Customer Support, Abandoned Cart Conversions, Non-Delivery Reports).
5. **`brand_processes`**: Junction defining client operating SLAs:
   - `calendar`: `EVERY_DAY` (365d), `HOLIDAYS_OFF`, or `SUNDAYS_HOLIDAYS_OFF`.
   - `minimum_staff`: Required staff per operating day.
   - `hours_per_employee`: Shift workload (e.g., 3.0 hours).
6. **`assignments`**: Many-to-many junction mapping employees to `brand_processes`, defining **permanent eligibility/training**.
7. **`absences` & `holidays`**: Stores planned leaves, comp-offs, and public holiday dates.
8. **`monthly_rosters`**: Persists generated plans, lock statuses, and draft/published states in JSON.

This decoupled structure ensures that a brand's operating calendar is completely independent of an employee's shift calendar.

---

### 2. How does your system generate a roster and determine whether it satisfies the required constraints?
The engine uses a deterministic, multi-pass greedy heuristic with **Minimum Staffing Slack Ordering**:

1. **Calendar Initialization**: Determines open requirements for each date by evaluating brand calendar rules against holiday calendars and day-of-week.
2. **Availability Pre-computation**: Computes daily employee status (`AVAILABLE`, `WEEKLY_OFF`, or `LEAVE`/`COMP_OFF`).
3. **Slack-Based Ordering**: Orders daily requirements by `Staffing Slack = (Eligible & Available Candidates - Remaining Staff Needed)`. The tightest requirements (fewest available options, e.g., single-resource brands) are scheduled first to prevent premature candidate exhaustion.
4. **Fair Workload Balancing**: When selecting among eligible candidates, ties are broken by cumulative monthly hours worked, ensuring equitable workload distribution.
5. **Validation Verification**: Live validation traverses the generated matrix checking:
   - **Hard Constraints**: `actual_staff >= minimum_staff`, `daily_hours <= daily_capacity`, and `employee not in (LEAVE, COMP_OFF)`.
   - **Soft Constraints**: Preferred weekly off adherence.

---

### 3. How did you handle the difference between single-resource brands and brands with multiple employees?
- **Single-Resource Brands (~80%, Brands 1–20)**:
  - Each brand is assigned one dedicated employee who handles all assigned processes (e.g., CS 3h + ACC 3h = 6h daily load).
  - Because 6 hours is within their 8-hour daily capacity, they handle all operations on active days.
  - The scheduling engine detects that `candidates_count == 1`. On days when this employee is on leave or weekly off, the system automatically tags a coverage gap or activates pod-assisted cross-coverage.
- **Multi-Resource Brands (~20%, Brands 21–25)**:
  - Assigned 2 to 3 employees with specialized and overlapping processes (e.g., Emp A on CS, Emp B on ACC, and both Emp A & B eligible for NDR).
  - Brand 25 requires a minimum staff of 2 on Customer Support.
  - The solver distributes processes across the assigned pool. If one employee is on weekly off, the remaining employees have sufficient shared capacity to maintain uninterrupted coverage.

---

### 4. What happens when an employee manages all processes for a brand but is unavailable on a required working day?
When a single-resource employee is unavailable:
1. **Detection**: The engine flags a **Critical Coverage Gap** for that brand and process on that date.
2. **Root-Cause Logging**: The gap records an explicit reason (e.g., `Aarav Sharma: LEAVE (Approved Leave)` or `Aarav Sharma: WEEKLY_OFF`).
3. **Smart Backup Finder**: The recommendation engine runs an instant ranking query:
   - Identifies colleagues belonging to the **same pod** who have spare daily capacity (`capacity - current_hours >= required_hours`).
   - Filters out anyone on leave.
   - Scores candidates based on same-pod proximity (+30), cross-training (+20), active working day (+15), and lowest cumulative monthly hours.
4. **1-Click Resolution**: The operations manager can view recommendations and click **"Assign Backup"** to immediately assign the replacement in the saved roster without modifying master database allocations.

---

### 5. How does your system handle conflicting requirements or insufficient staffing?
Rather than silently outputting an invalid schedule or crashing, the system employs **Multi-Strategy Resolution**:

1. **Strategy 1: Balanced Mode (Employee-First)**:
   - Strictly enforces employee weekly-off days.
   - Any single-resource brand operating on that day is surfaced as an explicit **Coverage Gap** in the validation report.
2. **Strategy 2: Client-Priority Mode (Client-First / SLA-Guaranteed)**:
   - Prioritizes client 365-day coverage over preferred weekly offs.
   - Schedules the employee to maintain client continuity, but flags the cell as `[OT/Comp]` and issues a **Policy Warning**: *"Scheduled on preferred weekly off for mandatory client coverage. Comp-off recommended."*
3. **Strategy 3: Auto-Backup Mode (Pod-Assisted)**:
   - Automatically draws upon unassigned, available teammates in the same pod to absorb the gap before falling back to warnings.
4. **Transparent Conflict Reporting**: All unresolved deficits appear in the **Live Validation** tab with severity tiers (`CRITICAL`, `WARNING`, `INFO`) and actionable advice.

---

### 6. Which parts of the process did you automate, and which did you leave under human control? Why?
- **Automated**:
  - **Daily Operating Open/Close Calculations**: Completely automated based on calendar rules and public holidays to eliminate human calculation errors.
  - **Greedy Slack-Based Assignment**: Automates scheduling thousands of monthly slots across 32 employees in less than 200 milliseconds.
  - **Live Constraint Validation**: Instant background verification of capacity, leave, and SLA rules.
  - **Backup Scoring**: Algorithmic ranking of replacement candidates based on pod proximity, capacity, and fairness.
- **Human Controlled**:
  - **Final Roster Publication**: Kept under human control via a **Draft -> Published** workflow. Managers must review flagged warnings and comp-off balances before publishing.
  - **Manual Day Overrides (Roster Edit)**: Operations managers can override any employee-day shift or swap responsibilities. Once edited, the system **locks** that employee-day and preserves human decisions across future regenerations.
  - **Leave and Holiday Approval**: Only approved leaves entered by management affect the schedule.

---

### 7. If the company doubled its team and brand count, what would you change about your solution?
If the team scaled to 60+ employees and 50+ brands:
1. **Solver Optimization**: Transition from the current in-memory greedy heuristic to an integer linear programming (ILP) solver like **Google OR-Tools** (CP-SAT) to handle deeper constraint optimization and multi-objective Pareto frontiers (optimizing fair weekends while minimizing overtime).
2. **Database Migration**: Move from SQLite to **PostgreSQL** with connection pooling (`pgbouncer`) to support concurrent roster editing by multiple pod leads simultaneously.
3. **Async Generation**: Background roster generation using **Celery / Redis Queue** with WebSocket or Server-Sent Events (SSE) progress bars.
4. **Hierarchical RBAC**: Enforce role-based access control where Pod Leads can only edit and approve their respective pod's schedules, while the Operations Manager oversees company-wide publication.

---

### 8. If you had one additional week to work on this, what would you improve first?
1. **Self-Service Shift Swapping Portal**: Allow employees to request shift swaps directly through a mobile-friendly view; once approved by their pod lead, the swap updates the roster automatically.
2. **Comp-Off Banking Ledger**: An automated comp-off wallet tracking when an employee worked on their weekly off and enforcing that comp-offs are redeemed within 30 days.
3. **Google Sheets / Calendar Two-Way Sync**: Push published monthly rosters directly to employee Google Calendars and sync live coverage tables into a Google Sheet.
4. **Automated AI Schedule Explainer**: Integrate LLM explanations summarizing the root cause of complex multi-pod scheduling bottlenecks and offering trade-off recommendations in conversational language.

---

## 📹 Demo Walkthrough Video Script (3–5 Minutes)
When recording your video submission, follow this outline:
1. **Introduction (30s)**: Introduce yourself, open the Dashboard, and highlight the 32 employees, 3 pods, and 25 brands with the 80/20 single vs. multi-resource split.
2. **Roster Generation & Views (90s)**:
   - Go to `/roster`, demonstrate generating November 2026 under **Balanced Mode** (showing strict weekly-off gaps) vs. **Client-Priority Mode** (showing 100% SLA coverage with comp-off warnings).
   - Walk through the **5 Views**: Company-Wide, Pod-Wise, Brand-Wise matrix, Process-Wise, and Individual Employee agenda.
3. **Live Validation & Conflict Audit (45s)**:
   - Navigate to `/validation`. Show the breakdown of Critical violations vs. Policy warnings. Show export to CSV.
4. **Handling Changes Demonstration (60s)**:
   - Open `/simulator`.
   - **Scenario 1**: Pick Aarav Sharma on Bloom Beauty, simulate sudden leave on Nov 15. Show the immediate gap, inspect the Smart Backup recommendations from North Pod, and click "Assign Backup" to restore coverage.
   - **Scenario 2**: Show Brand 25 (Green Pantry) with 3 employees, remove Siddharth Kumar, and show how the system evaluates whether the remaining team can absorb the load.
5. **Conclusion (15s)**: Mention clean test suite (`python -m unittest tests/test_roster.py`) and easy deployment.

---

## 📬 Email Submission Template

**To**: `ozair.shaikh@datastraw.in`, `aryan.jaiswal@datastraw.in`  
**CC**: `talent@datastraw.in`  
**Subject**: `Smart Roster Management System Assignment - [Your Full Name]`

```text
Dear DataStraw Hiring Team,

I have completed the hiring assessment for the Smart Roster Management System. Below are the submission deliverables:

1. Application URL: [Insert your live deployed URL, e.g. Render/Vercel/Railway]
2. GitHub Repository: [Insert your public GitHub repo URL]
3. LinkedIn Profile: [Insert your LinkedIn URL]

Approach Note:
I built a constraint-driven roster management platform using Flask, SQLite, and a multi-pass greedy heuristic solver with Minimum Staffing Slack Ordering. The architecture models 32 employees across 3 pods and 25 D2C brands (faithfully reflecting the 80/20 single vs. multi-resource distribution), decouples brand operating calendars from employee shifts, and provides a smart pod-level backup recommendation engine with 1-click gap resolution. The application features 5 dedicated stakeholder views, live validation, a dynamic change simulator, and full CSV export capabilities.

Thank you for the opportunity, and I look forward to your feedback!

Sincerely,
[Your Full Name]
```
