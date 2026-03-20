# database_init.py
# ============================================================
# Nutrabay Interview Scheduler - Database Initialization
# Run this ONCE before launching the app: python database_init.py
# ============================================================

import sqlite3
import json
import random
from datetime import datetime, timedelta, date

DB_PATH = "nutrabay_interviews.db"


def init_db():
    """Create all tables and seed with realistic sample data."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ----------------------------------------------------------
    # TABLE: employees
    # Stores all Nutrabay employees who can be interviewers
    # ----------------------------------------------------------
    c.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            name                  TEXT    NOT NULL,
            email                 TEXT    UNIQUE NOT NULL,
            department            TEXT    NOT NULL,
            team                  TEXT    NOT NULL,
            subteam               TEXT,
            designation           TEXT    NOT NULL,
            round_type_preference TEXT    NOT NULL  -- Technical | HR | Manager
        )
    ''')

    # ----------------------------------------------------------
    # TABLE: availability
    # Each row = one FREE time window for an employee
    # (Not a busy slot — these are AVAILABLE windows)
    # ----------------------------------------------------------
    c.execute('''
        CREATE TABLE IF NOT EXISTS availability (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL,
            date        TEXT    NOT NULL,   -- YYYY-MM-DD
            start_time  TEXT    NOT NULL,   -- HH:MM (24h)
            end_time    TEXT    NOT NULL,   -- HH:MM (24h)
            FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
        )
    ''')

    # ----------------------------------------------------------
    # TABLE: interviews
    # Stores every interview request and its scheduling outcome
    # ----------------------------------------------------------
    c.execute('''
        CREATE TABLE IF NOT EXISTS interviews (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_name       TEXT NOT NULL,
            candidate_email      TEXT NOT NULL,
            round_type           TEXT NOT NULL,             -- Technical | HR | Manager
            position_department  TEXT NOT NULL,
            position_team        TEXT NOT NULL,
            position_subteam     TEXT,
            selected_interviewers TEXT NOT NULL,            -- JSON array of employee dicts
            scheduled_slot       TEXT,                      -- JSON {date, start_time, end_time, duration}
            status               TEXT DEFAULT 'Pending',    -- Pending | Scheduled | Cancelled | Completed
            reasoning            TEXT,                      -- AI-generated recommendation text
            created_at           TEXT DEFAULT (datetime('now','localtime'))
        )
    ''')

    conn.commit()

    # ----------------------------------------------------------
    # SEED: Employee data (realistic Nutrabay org hierarchy)
    # ----------------------------------------------------------
    employees = [
        # ── Technology › Backend › DevOps ────────────────────
        ("Rahul Sharma",    "rahul.sharma@nutrabay.com",    "Technology", "Backend",  "DevOps",       "Senior DevOps Engineer",    "Technical"),
        ("Priya Patel",     "priya.patel@nutrabay.com",     "Technology", "Backend",  "DevOps",       "DevOps Tech Lead",          "Technical"),

        # ── Technology › Backend › API ────────────────────────
        ("Amit Kumar",      "amit.kumar@nutrabay.com",      "Technology", "Backend",  "API",          "Backend Engineer",          "Technical"),
        ("Sneha Gupta",     "sneha.gupta@nutrabay.com",     "Technology", "Backend",  "API",          "Senior Backend Engineer",   "Technical"),
        ("Rohit Bansal",    "rohit.bansal@nutrabay.com",    "Technology", "Backend",  "API",          "Staff Engineer",            "Technical"),

        # ── Technology › Frontend › React ─────────────────────
        ("Vikram Singh",    "vikram.singh@nutrabay.com",    "Technology", "Frontend", "React",        "Frontend Engineer",         "Technical"),
        ("Neha Joshi",      "neha.joshi@nutrabay.com",      "Technology", "Frontend", "React",        "Senior Frontend Engineer",  "Technical"),
        ("Pooja Verma",     "pooja.verma@nutrabay.com",     "Technology", "Frontend", "React",        "UI/UX + React Lead",        "Technical"),

        # ── Technology › AI › AI-ML ───────────────────────────
        ("Arjun Mehta",     "arjun.mehta@nutrabay.com",     "Technology", "AI",       "AI-ML",        "ML Engineer",               "Technical"),
        ("Kavya Reddy",     "kavya.reddy@nutrabay.com",     "Technology", "AI",       "AI-ML",        "Senior Data Scientist",     "Technical"),

        # ── Technology › AI › AI-Nutrabay ─────────────────────
        ("Rohan Das",       "rohan.das@nutrabay.com",       "Technology", "AI",       "AI-Nutrabay",  "AI Product Engineer",       "Technical"),
        ("Simran Kaur",     "simran.kaur@nutrabay.com",     "Technology", "AI",       "AI-Nutrabay",  "NLP Engineer",              "Technical"),

        # ── Technology › Product › Enterprise Tools ───────────
        ("Sanya Kapoor",    "sanya.kapoor@nutrabay.com",    "Technology", "Product",  "Enterprise Tools", "Product Manager",       "Manager"),
        ("Dev Malhotra",    "dev.malhotra@nutrabay.com",    "Technology", "Product",  "Enterprise Tools", "Associate PM",          "Technical"),

        # ── HR › Talent Acquisition ───────────────────────────
        ("Divya Nair",      "divya.nair@nutrabay.com",      "HR",         "Talent Acquisition", "Engineering Hiring", "HR Manager",         "HR"),
        ("Ananya Sharma",   "ananya.sharma@nutrabay.com",   "HR",         "Talent Acquisition", "Engineering Hiring", "HR Executive",       "HR"),
        ("Kiran Rao",       "kiran.rao@nutrabay.com",       "HR",         "People Ops",         "Culture",            "HR Business Partner","HR"),

        # ── Management › Engineering ──────────────────────────
        ("Suresh Iyer",     "suresh.iyer@nutrabay.com",     "Management", "Engineering", "Backend",   "Engineering Manager",       "Manager"),
        ("Meena Krishnan",  "meena.krishnan@nutrabay.com",  "Management", "Engineering", "Frontend",  "Engineering Manager",       "Manager"),
        ("Tarun Chopra",    "tarun.chopra@nutrabay.com",    "Management", "Engineering", "AI",        "Head of AI",                "Manager"),

        # ── Management › Product ──────────────────────────────
        ("Rajesh Verma",    "rajesh.verma@nutrabay.com",    "Management", "Product",  "Growth",       "Product Director",          "Manager"),

        # ── Sales ─────────────────────────────────────────────
        ("Pooja Agarwal",   "pooja.agarwal@nutrabay.com",   "Sales",      "Enterprise","B2B",         "Sales Manager",             "Manager"),

        # ── Marketing ─────────────────────────────────────────
        ("Tushar Malhotra", "tushar.malhotra@nutrabay.com", "Marketing",  "Digital",  "Performance",  "Marketing Lead",            "Technical"),
    ]

    inserted_ids = []
    for emp in employees:
        try:
            cursor = c.execute(
                '''INSERT INTO employees
                   (name, email, department, team, subteam, designation, round_type_preference)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''', emp
            )
            inserted_ids.append(cursor.lastrowid)
        except sqlite3.IntegrityError:
            # Employee already exists — fetch their ID
            existing = c.execute(
                "SELECT id FROM employees WHERE email = ?", (emp[1],)
            ).fetchone()
            if existing:
                inserted_ids.append(existing[0])

    conn.commit()

    # ----------------------------------------------------------
    # SEED: Availability slots for next 10 business days
    # Each employee gets randomised free windows
    # ----------------------------------------------------------
    time_windows = [
        ("09:00", "11:00"), ("09:30", "11:30"), ("10:00", "12:00"),
        ("11:00", "13:00"), ("14:00", "16:00"), ("14:30", "16:30"),
        ("15:00", "17:00"), ("16:00", "18:00"), ("10:00", "11:30"),
        ("13:00", "15:00"),
    ]

    today = date.today()
    for emp_id in inserted_ids:
        # Check if availability already seeded
        existing = c.execute(
            "SELECT COUNT(*) FROM availability WHERE employee_id = ?", (emp_id,)
        ).fetchone()[0]
        if existing > 0:
            continue

        for day_offset in range(1, 11):
            slot_date = today + timedelta(days=day_offset)
            if slot_date.weekday() >= 5:          # Skip weekends
                continue
            # Give each employee 2–4 free windows per day
            chosen = random.sample(time_windows, random.randint(2, 4))
            for (start, end) in chosen:
                c.execute(
                    "INSERT INTO availability (employee_id, date, start_time, end_time) VALUES (?, ?, ?, ?)",
                    (emp_id, slot_date.strftime('%Y-%m-%d'), start, end)
                )

    conn.commit()
    conn.close()
    print(f"✅ Database '{DB_PATH}' initialised with {len(employees)} employees and sample availability.")


if __name__ == "__main__":
    init_db()
