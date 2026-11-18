import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "instance" / "roster.db"


def get_connection():
    DATABASE_PATH.parent.mkdir(exist_ok=True)

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def initialise_database():
    connection = get_connection()

    try:
        with connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS pods (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    pod_id INTEGER NOT NULL,
                    is_pod_lead INTEGER NOT NULL DEFAULT 0
                        CHECK (is_pod_lead IN (0, 1)),
                    weekly_off INTEGER NOT NULL DEFAULT 6
                        CHECK (weekly_off BETWEEN 0 AND 6),
                    daily_capacity_hours INTEGER NOT NULL DEFAULT 8
                        CHECK (daily_capacity_hours BETWEEN 1 AND 24),
                    FOREIGN KEY (pod_id) REFERENCES pods(id)
                );

                CREATE TABLE IF NOT EXISTS absences (
                    id INTEGER PRIMARY KEY,
                    employee_id INTEGER NOT NULL,
                    absence_date TEXT NOT NULL,
                    absence_type TEXT NOT NULL CHECK (
                        absence_type IN ('LEAVE', 'COMP_OFF', 'UNAVAILABLE')
                    ),
                    UNIQUE (employee_id, absence_date),
                    FOREIGN KEY (employee_id) REFERENCES employees(id)
                );

                CREATE TABLE IF NOT EXISTS holidays (
                    holiday_date TEXT PRIMARY KEY,
                    name TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS staffing_overrides (
                    brand_process_id INTEGER NOT NULL REFERENCES brand_processes(id),
                    work_date TEXT NOT NULL,
                    minimum_staff INTEGER NOT NULL CHECK (minimum_staff >= 1),
                    PRIMARY KEY (brand_process_id, work_date)
                );

                CREATE TABLE IF NOT EXISTS monthly_rosters (
                    month TEXT PRIMARY KEY,
                    roster_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'PUBLISHED')),
                    strategy TEXT NOT NULL DEFAULT 'BALANCED',
                    published_at TEXT,
                    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

            employee_count = connection.execute(
                "SELECT COUNT(*) FROM employees"
            ).fetchone()[0]

            # Add sample data only when the employee table is empty.
            if employee_count == 0:
                pods = [
                    (1, "North Pod"),
                    (2, "South Pod"),
                    (3, "Central Pod"),
                ]

                connection.executemany(
                    "INSERT OR IGNORE INTO pods (id, name) VALUES (?, ?)",
                    pods,
                )

                names = [
                    "Aarav Sharma", "Diya Patel", "Kabir Shah",
                    "Meera Nair", "Arjun Rao", "Isha Mehta",
                    "Rohan Das", "Ananya Iyer", "Vivaan Joshi",
                    "Saanvi Kapoor", "Aditya Menon", "Kavya Singh",
                    "Krish Verma", "Priya Pillai", "Rahul Gupta",
                    "Neha Reddy", "Dev Malhotra", "Tara Bhat",
                    "Yash Kulkarni", "Riya Sinha", "Nikhil Jain",
                    "Pooja Mishra", "Karan Bose", "Sneha Roy",
                    "Varun Desai", "Aditi Pandey", "Manav Sethi",
                    "Nisha Thomas", "Siddharth Kumar", "Anika Dutta",
                    "Pranav Shetty", "Zoya Khan",
                ]

                employees = []

                for index, name in enumerate(names):
                    employees.append((
                        name,
                        (index % 3) + 1,
                        1 if index < 3 else 0,
                        index % 7,
                        8,
                    ))

                connection.executemany("""
                    INSERT INTO employees (
                        name,
                        pod_id,
                        is_pod_lead,
                        weekly_off,
                        daily_capacity_hours
                    )
                    VALUES (?, ?, ?, ?, ?)
                """, employees)
    finally:
        connection.close()


def get_employees():
    connection = get_connection()

    try:
        return connection.execute("""
            SELECT
                employees.id,
                employees.name,
                employees.pod_id,
                pods.name AS pod_name,
                employees.is_pod_lead,
                employees.weekly_off,
                employees.daily_capacity_hours
            FROM employees
            JOIN pods ON employees.pod_id = pods.id
            ORDER BY employees.id
        """).fetchall()
    finally:
        connection.close()


def get_single_employee(employee_id):
    connection = get_connection()

    try:
        return connection.execute("""
            SELECT
                employees.id,
                employees.name,
                employees.pod_id,
                pods.name AS pod_name,
                employees.is_pod_lead,
                employees.weekly_off,
                employees.daily_capacity_hours
            FROM employees
            JOIN pods ON employees.pod_id = pods.id
            WHERE employees.id = ?
        """, (employee_id,)).fetchone()
    finally:
        connection.close()


def get_pods():
    connection = get_connection()

    try:
        pods = connection.execute("""
            SELECT
                p.id,
                p.name,
                COUNT(e.id) AS member_count,
                (SELECT name FROM employees WHERE pod_id = p.id AND is_pod_lead = 1 LIMIT 1) AS lead_name
            FROM pods p
            LEFT JOIN employees e ON e.pod_id = p.id
            GROUP BY p.id
            ORDER BY p.id
        """).fetchall()
        return pods
    finally:
        connection.close()


def add_employee(name, pod_id, is_pod_lead=0, weekly_off=6, daily_capacity_hours=8):
    connection = get_connection()
    try:
        with connection:
            cursor = connection.execute("""
                INSERT INTO employees (name, pod_id, is_pod_lead, weekly_off, daily_capacity_hours)
                VALUES (?, ?, ?, ?, ?)
            """, (name.strip(), pod_id, 1 if is_pod_lead else 0, weekly_off, daily_capacity_hours))
            return cursor.lastrowid
    finally:
        connection.close()


def add_brand(name, process_configs):
    """
    process_configs: list of dicts:
    [{'process_id': 1, 'calendar': 'EVERY_DAY', 'minimum_staff': 1, 'hours': 3.0, 'employee_ids': [1, 2]}]
    """
    connection = get_connection()
    try:
        with connection:
            cursor = connection.execute("INSERT INTO brands (name) VALUES (?)", (name.strip(),))
            brand_id = cursor.lastrowid

            for config in process_configs:
                proc_cursor = connection.execute("""
                    INSERT INTO brand_processes (brand_id, process_id, calendar, minimum_staff, hours_per_employee)
                    VALUES (?, ?, ?, ?, ?)
                """, (brand_id, config['process_id'], config['calendar'], config.get('minimum_staff', 1), config.get('hours', 3.0)))
                requirement_id = proc_cursor.lastrowid

                for emp_id in config.get('employee_ids', []):
                    connection.execute("""
                        INSERT INTO assignments (employee_id, brand_process_id)
                        VALUES (?, ?)
                    """, (emp_id, requirement_id))

            return brand_id
    finally:
        connection.close()


def get_processes():
    connection = get_connection()
    try:
        return connection.execute("SELECT * FROM processes ORDER BY id").fetchall()
    finally:
        connection.close()


def initialise_brand_data():
    connection = get_connection()

    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS brands (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS processes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS brand_processes (
                id INTEGER PRIMARY KEY,
                brand_id INTEGER NOT NULL,
                process_id INTEGER NOT NULL,
                calendar TEXT NOT NULL CHECK (
                    calendar IN (
                        'EVERY_DAY',
                        'HOLIDAYS_OFF',
                        'SUNDAYS_HOLIDAYS_OFF'
                    )
                ),
                minimum_staff INTEGER NOT NULL DEFAULT 1
                    CHECK (minimum_staff >= 1),
                hours_per_employee REAL NOT NULL DEFAULT 3
                    CHECK (hours_per_employee > 0),
                UNIQUE (brand_id, process_id),
                FOREIGN KEY (brand_id) REFERENCES brands(id),
                FOREIGN KEY (process_id) REFERENCES processes(id)
            );

            CREATE TABLE IF NOT EXISTS assignments (
                employee_id INTEGER NOT NULL,
                brand_process_id INTEGER NOT NULL,
                PRIMARY KEY (employee_id, brand_process_id),
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (brand_process_id)
                    REFERENCES brand_processes(id)
            );
        """)

        with connection:
            brand_count = connection.execute(
                "SELECT COUNT(*) FROM brands"
            ).fetchone()[0]

            if brand_count == 0:
                brand_names = [
                    "Bloom Beauty", "Orbit Wear", "Cedar Home",
                    "Nova Nutrition", "Loom Living", "Pure Petals",
                    "Urban Trail", "Glow Craft", "Fresh Basket",
                    "Cozy Corner", "Zen Essentials", "Peak Fitness",
                    "Little Sprouts", "Terra Goods", "Velvet Vogue",
                    "Daily Harvest", "Ocean Blue", "Maple Studio",
                    "Bright Nest", "Root Wellness", "Swift Style",
                    "Aura Care", "Happy Paws", "Craft Avenue",
                    "Green Pantry",
                ]

                connection.executemany(
                    "INSERT INTO brands (id, name) VALUES (?, ?)",
                    list(enumerate(brand_names, start=1)),
                )

                connection.executemany(
                    "INSERT INTO processes (id, name) VALUES (?, ?)",
                    [
                        (1, "Customer Support"),
                        (2, "Abandoned Cart Conversions"),
                        (3, "Non-Delivery Reports"),
                    ],
                )

                calendars = [
                    "EVERY_DAY",
                    "HOLIDAYS_OFF",
                    "SUNDAYS_HOLIDAYS_OFF",
                ]

                for brand_id in range(1, 26):
                    # The final five brands also have an NDR process.
                    process_ids = (
                        [1, 2] if brand_id <= 20 else [1, 2, 3]
                    )

                    for process_id in process_ids:
                        # Different processes can have different calendars.
                        calendar = calendars[
                            (brand_id + process_id - 2) % 3
                        ]

                        minimum_staff = (
                            2
                            if brand_id == 25 and process_id == 1
                            else 1
                        )

                        cursor = connection.execute("""
                            INSERT INTO brand_processes (
                                brand_id,
                                process_id,
                                calendar,
                                minimum_staff,
                                hours_per_employee
                            )
                            VALUES (?, ?, ?, ?, ?)
                        """, (
                            brand_id,
                            process_id,
                            calendar,
                            minimum_staff,
                            3,
                        ))

                        requirement_id = cursor.lastrowid

                        if brand_id <= 20:
                            # One employee handles both processes.
                            employee_ids = [brand_id]
                        else:
                            first = 21 + (brand_id - 21) * 2
                            second = first + 1

                            if process_id == 1:
                                employee_ids = [first]
                            elif process_id == 2:
                                employee_ids = [second]
                            else:
                                # Both employees are eligible for NDR.
                                employee_ids = [first, second]

                            if brand_id == 24 and process_id == 1:
                                employee_ids.append(31)

                            if brand_id == 25 and process_id == 1:
                                employee_ids.append(32)

                        connection.executemany("""
                            INSERT INTO assignments (
                                employee_id,
                                brand_process_id
                            )
                            VALUES (?, ?)
                        """, [
                            (employee_id, requirement_id)
                            for employee_id in employee_ids
                        ])
    finally:
        connection.close()


def get_brand_processes():
    connection = get_connection()

    try:
        return connection.execute("""
            SELECT
                bp.id,
                bp.brand_id,
                bp.process_id,
                brands.name AS brand_name,
                processes.name AS process_name,
                bp.calendar,
                bp.minimum_staff,
                bp.hours_per_employee,
                GROUP_CONCAT(employees.name, ', ') AS employee_names,
                GROUP_CONCAT(employees.id, ',') AS employee_ids
            FROM brand_processes AS bp
            JOIN brands ON brands.id = bp.brand_id
            JOIN processes ON processes.id = bp.process_id
            LEFT JOIN assignments
                ON assignments.brand_process_id = bp.id
            LEFT JOIN employees
                ON employees.id = assignments.employee_id
            GROUP BY bp.id
            ORDER BY brands.id, processes.id
        """).fetchall()
    finally:
        connection.close()
