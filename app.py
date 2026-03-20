# app.py
# ============================================================
# Nutrabay Interview Scheduling Automation — MVP
# Built with Streamlit + SQLite + Google Gemini AI
#
# Pages:
#   1. 🎯 Schedule Interview   — Candidate submits availability
#   2. 📅 Manage Availability  — Employees log free slots
#   3. 📊 HR Dashboard         — View / reschedule all interviews
#   4. ⚙️  Admin Panel          — CRUD for employee database
# ============================================================

import os
import re
import json
import sqlite3
import random
from datetime import datetime, timedelta, date, time
from typing import List, Dict, Optional, Tuple, Any

import streamlit as st
import pandas as pd

# ── Gemini (optional — graceful fallback if key absent) ─────
from dotenv import load_dotenv

load_dotenv()  # Loads variables from .env into os.environ

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Read API key once at startup — never from UI
_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# ────────────────────────────────────────────────────────────
# GLOBAL CONSTANTS
# ────────────────────────────────────────────────────────────

DB_PATH = "nutrabay_interviews.db"

NUTRABAY_GREEN  = "#27AE60"
NUTRABAY_LIGHT  = "#2ECC71"
NUTRABAY_BG     = "#F0FFF4"
NUTRABAY_DARK   = "#1B4332"

DEPARTMENTS = ["Technology", "HR", "Management", "Sales", "Marketing", "Finance", "Operations"]
ROUNDS      = ["Technical", "HR", "Manager"]

# Interviewer selection weights (hard-coded per spec)
WEIGHT_SAME_SUBTEAM_SAME_ROUND  = 10
WEIGHT_SAME_TEAM_SAME_ROUND     = 7
WEIGHT_SAME_DEPT_SAME_ROUND     = 4
WEIGHT_ANY_SAME_ROUND           = 2
WEIGHT_SAME_DEPT_DIFF_ROUND     = 1      # tie-break only


# ────────────────────────────────────────────────────────────
# PAGE CONFIG & GLOBAL STYLING
# ────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Nutrabay Interview Scheduler",
    page_icon="🥗",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
    /* ── Global typography ── */
    html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}

    /* ── Nutrabay top header banner ── */
    .nb-header {{
        background: linear-gradient(135deg, {NUTRABAY_DARK}, {NUTRABAY_GREEN});
        padding: 22px 28px; border-radius: 12px; color: white;
        text-align: center; margin-bottom: 24px;
        box-shadow: 0 4px 15px rgba(39,174,96,0.3);
    }}
    .nb-header h1 {{ margin: 0; font-size: 1.9rem; font-weight: 700; }}
    .nb-header p  {{ margin: 6px 0 0; opacity: 0.85; font-size: 0.95rem; }}

    /* ── Slot recommendation card ── */
    .slot-card {{
        background: {NUTRABAY_BG};
        border: 1.5px solid {NUTRABAY_GREEN};
        border-radius: 10px; padding: 16px 20px; margin: 10px 0;
        transition: box-shadow .2s;
    }}
    .slot-card:hover {{ box-shadow: 0 4px 12px rgba(39,174,96,0.25); }}

    /* ── Interviewer score badge ── */
    .score-badge {{
        display: inline-block; padding: 3px 10px; border-radius: 20px;
        font-size: 11px; font-weight: 600; margin: 2px;
    }}

    /* ── Metric cards ── */
    .metric-row {{ display: flex; gap: 14px; margin-bottom: 20px; }}
    .metric-box {{
        flex: 1; background: white; border-radius: 10px; padding: 16px;
        border-left: 4px solid {NUTRABAY_GREEN};
        box-shadow: 0 2px 8px rgba(0,0,0,0.06);
    }}
    .metric-box .val {{ font-size: 2rem; font-weight: 700; color: {NUTRABAY_GREEN}; }}
    .metric-box .lbl {{ font-size: 0.8rem; color: #666; margin-top: 2px; }}

    /* ── Primary button ── */
    div.stButton > button[kind="primary"],
    div.stButton > button {{
        background: {NUTRABAY_GREEN} !important;
        color: white !important; border: none !important;
        border-radius: 8px !important; font-weight: 600 !important;
    }}
    div.stButton > button:hover {{
        background: {NUTRABAY_LIGHT} !important;
        box-shadow: 0 3px 10px rgba(39,174,96,0.4) !important;
    }}

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {{ background: #F8FFF9; }}

    /* ── Confidence pill ── */
    .conf-pill {{
        display: inline-block; padding: 2px 10px; border-radius: 20px;
        font-size: 12px; font-weight: 700;
        background: {NUTRABAY_BG}; border: 1px solid {NUTRABAY_GREEN};
        color: {NUTRABAY_DARK};
    }}
</style>
""", unsafe_allow_html=True)


# ────────────────────────────────────────────────────────────
# ██  DATABASE LAYER
# ────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    """Return a sqlite3 connection with Row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@st.cache_data(ttl=30)
def fetch_all_employees() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM employees ORDER BY department, team, name"
        ).fetchall()
    return [dict(r) for r in rows]


def fetch_employee(emp_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    return dict(row) if row else None


def fetch_availability(emp_id: int,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None) -> List[Dict]:
    query  = "SELECT * FROM availability WHERE employee_id=?"
    params: list = [emp_id]
    if start_date:
        query  += " AND date >= ?"; params.append(start_date)
    if end_date:
        query  += " AND date <= ?"; params.append(end_date)
    query += " ORDER BY date, start_time"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def upsert_availability(emp_id: int, date_str: str, start: str, end: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO availability (employee_id, date, start_time, end_time) VALUES (?,?,?,?)",
            (emp_id, date_str, start, end)
        )
    st.cache_data.clear()


def remove_availability(avail_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM availability WHERE id=?", (avail_id,))
    st.cache_data.clear()


def create_interview(candidate_name: str, candidate_email: str,
                     round_type: str, dept: str, team: str, subteam: str,
                     interviewers: List[Dict], slot: Dict, reasoning: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO interviews
               (candidate_name, candidate_email, round_type,
                position_department, position_team, position_subteam,
                selected_interviewers, scheduled_slot, status, reasoning)
               VALUES (?,?,?,?,?,?,?,?,'Scheduled',?)""",
            (candidate_name, candidate_email, round_type, dept, team, subteam,
             json.dumps(interviewers), json.dumps(slot), reasoning)
        )
        # Email placeholder
        print(f"[EMAIL] Interview confirmation sent to {candidate_email} "
              f"for {slot.get('date')} {slot.get('start_time')}-{slot.get('end_time')}")
    st.cache_data.clear()
    return cur.lastrowid


@st.cache_data(ttl=10)
def fetch_all_interviews() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM interviews ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def patch_interview(interview_id: int, status: str,
                    new_slot: Optional[Dict] = None,
                    new_interviewers: Optional[List] = None,
                    reasoning: Optional[str] = None) -> None:
    with get_conn() as conn:
        if new_slot and new_interviewers:
            conn.execute(
                """UPDATE interviews
                   SET status=?, scheduled_slot=?, selected_interviewers=?, reasoning=?
                   WHERE id=?""",
                (status, json.dumps(new_slot), json.dumps(new_interviewers), reasoning, interview_id)
            )
        elif new_slot:
            conn.execute(
                "UPDATE interviews SET status=?, scheduled_slot=?, reasoning=? WHERE id=?",
                (status, json.dumps(new_slot), reasoning, interview_id)
            )
        else:
            conn.execute("UPDATE interviews SET status=? WHERE id=?", (status, interview_id))
    st.cache_data.clear()


def add_employee_db(name, email, dept, team, subteam, designation, round_pref) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO employees
                   (name,email,department,team,subteam,designation,round_type_preference)
                   VALUES (?,?,?,?,?,?,?)""",
                (name, email, dept, team, subteam, designation, round_pref)
            )
        st.cache_data.clear()
        return True
    except sqlite3.IntegrityError:
        return False


def remove_employee_db(emp_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM employees WHERE id=?", (emp_id,))
        conn.execute("DELETE FROM availability WHERE employee_id=?", (emp_id,))
    st.cache_data.clear()


# ────────────────────────────────────────────────────────────
# ██  GEMINI AI LAYER
# ────────────────────────────────────────────────────────────

def _get_gemini_model():
    """Return a configured Gemini GenerativeModel or None."""
    if not _GEMINI_API_KEY or not GEMINI_AVAILABLE:
        return None
    try:
        genai.configure(api_key=_GEMINI_API_KEY)
        return genai.GenerativeModel("gemini-2.5-flash")
    except Exception as e:
        st.warning(f"Gemini model init failed: {e}")
        return None

def gemini_parse_availability(text: str, ref_date: Optional[date] = None) -> List[Dict]:
    """
    Use Gemini to convert natural-language availability text into
    a structured list: [{"date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM"}]
    Falls back to regex parser on any error.
    """
    if ref_date is None:
        ref_date = date.today()

    model = _get_gemini_model()
    if not model:
        return _fallback_parse_availability(text, ref_date)

    prompt = f"""
Today's date is {ref_date.strftime('%A, %B %d, %Y')} (ISO: {ref_date.isoformat()}).

Parse the following availability text into structured JSON time slots.
Return ONLY a valid JSON array — no explanation, no markdown fences.

Each element must be:
{{"date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM"}}

Rules:
- Convert day names (Mon, Tue, Wed…) to ACTUAL calendar dates relative to today
- "next week" = week starting next Monday
- "this week" or no qualifier = dates starting tomorrow
- Convert 12-hour time to 24-hour (e.g. 2 PM → 14:00, 9 AM → 09:00)
- If a range spans multiple days (e.g. Tue–Thu 2–5 PM), generate one slot per day
- Minimum slot duration: 30 minutes

Input: "{text}"

Return only the JSON array:
""".strip()

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        # Strip any markdown code fences if present
        raw = re.sub(r"```(?:json)?", "", raw).strip("` \n")
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        return []
    except Exception as e:
        st.warning(f"Gemini parse error — using fallback: {e}")
        return _fallback_parse_availability(text, ref_date)


def gemini_generate_reasoning(slot: Dict, interviewers: List[Dict],
                               candidate: str, position: str) -> str:
    """
    Use Gemini to generate a professional one-sentence reasoning string
    for why this particular slot is recommended.
    """
    model = _get_gemini_model()
    names = ", ".join(i.get("name", "Unknown") for i in interviewers)
    fallback = (
        f"Optimal {slot['duration']}-minute window on {slot['day']} "
        f"with {len(interviewers)} subject-matter experts available."
    )
    if not model:
        return fallback

    prompt = (
        f"Write one professional sentence (max 30 words) explaining why "
        f"{slot['day']} {slot['date']} at {slot['start_time']} is the best interview slot "
        f"for {candidate} applying to {position}, with interviewers: {names}. "
        f"No quotes, no extra text."
    )
    try:
        r = model.generate_content(prompt)
        return r.text.strip().strip('"').strip("'")
    except Exception:
        return fallback


# ────────────────────────────────────────────────────────────
# ██  FALLBACK NLP AVAILABILITY PARSER (regex-based)
# ────────────────────────────────────────────────────────────

_DAY_MAP = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


def _resolve_date(day_token: str, ref: date, next_week: bool) -> Optional[date]:
    weekday = _DAY_MAP.get(day_token.lower())
    if weekday is None:
        return None
    if next_week:
        # Jump to next Monday first
        days_to_monday = (7 - ref.weekday()) % 7 or 7
        monday = ref + timedelta(days=days_to_monday)
        return monday + timedelta(days=weekday)
    else:
        days_ahead = (weekday - ref.weekday()) % 7 or 7
        return ref + timedelta(days=days_ahead)


def _parse_time_token(token: str) -> Optional[str]:
    """Convert a time token like '2pm', '14:00', '9 AM' to 'HH:MM'."""
    token = token.strip().lower()
    m = re.match(r'^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$', token)
    if not m:
        return None
    h, mins, period = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if period == "pm" and h != 12:
        h += 12
    elif period == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mins:02d}"


def _fallback_parse_availability(text: str, ref: date) -> List[Dict]:
    """
    Regex-based fallback parser.
    Handles patterns like:
      "Tue 2–5 PM", "Wed–Fri 9 AM–12 PM", "Mon, Thu 10-12", "next week Tue 2-4 PM"
    """
    slots   = []
    tl      = text.lower()
    nxt_wk  = "next week" in tl

    # Pattern: one or more day names, then time range
    # e.g. "tue–thu 2–5 pm"  or  "mon, wed 9am-1pm"
    day_pattern  = r'((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*)'
    range_pattern = (
        r'(\d{1,2}(?::\d{2})?)\s*([ap]m)?\s*'
        r'[-–to]+\s*'
        r'(\d{1,2}(?::\d{2})?)\s*([ap]m)?'
    )

    # Find all day tokens and their positions
    for day_match in re.finditer(
            r'((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*)'
            r'(?:\s*[-–,/]\s*((?:mon|tue|wed|thu|fri|sat|sun)[a-z]*))?'
            r'[,\s]*' + range_pattern,
            tl):
        day1_tok  = day_match.group(1)
        day2_tok  = day_match.group(2)   # optional range end-day
        s_val     = day_match.group(3)
        s_period  = day_match.group(4) or ""
        e_val     = day_match.group(5)
        e_period  = day_match.group(6) or ""

        # Infer AM/PM if only end has a period
        if e_period and not s_period:
            s_period = e_period

        s_time = _parse_time_token(s_val + s_period)
        e_time = _parse_time_token(e_val + e_period)
        if not s_time or not e_time:
            continue

        # Build the list of days
        day1 = _DAY_MAP.get(day1_tok[:3])
        day2 = _DAY_MAP.get(day2_tok[:3]) if day2_tok else day1
        if day1 is None:
            continue
        if day2 is None:
            day2 = day1

        day_range = (
            range(day1, day2 + 1) if day2 >= day1
            else list(range(day1, 5)) + list(range(0, day2 + 1))
        )
        for wd in day_range:
            # Map weekday number back to 3-letter key
            rev = {v: k for k, v in _DAY_MAP.items() if len(k) == 3}
            slot_date = _resolve_date(rev.get(wd, "mon"), ref, nxt_wk)
            if slot_date:
                slots.append({
                    "date":       slot_date.strftime("%Y-%m-%d"),
                    "start_time": s_time,
                    "end_time":   e_time,
                })

    return slots


# ────────────────────────────────────────────────────────────
# ██  INTERVIEWER SELECTION ENGINE
# ────────────────────────────────────────────────────────────
def get_scored_interviewers_cached(
    dept:    str,
    team:    str,
    subteam: Optional[str],
    round_type: str,
) -> List[Dict]:
    """
    Run AI/rule scoring ONCE per unique (dept, team, subteam, round) combo.
    Result is stored in st.session_state — survives re-renders but resets
    on new combinations so stale scores are never shown.
    """
    cache_key = f"scored__{dept}__{team}__{subteam or 'any'}__{round_type}"

    if cache_key not in st.session_state:
        all_emps = fetch_all_employees()
        with st.spinner("🤖 AI is matching interviewers… (runs once per selection)"):
            result = score_and_rank_interviewers(
                all_emps, dept, team, subteam, round_type
            )
        st.session_state[cache_key] = result
        # Store last used key so we can display cache-hit indicator
        st.session_state["last_score_cache_key"] = cache_key

    return st.session_state[cache_key]

def score_and_rank_interviewers(
    employees:    List[Dict],
    target_dept:  str,
    target_team:  str,
    target_subteam: Optional[str],
    round_type:   str,
) -> List[Dict]:
    """
    Score every employee by the weighted preference system and return
    a sorted list of {"employee": dict, "score": int, "match_reason": str}.

    Weights:
      10 → same subteam   + same round preference
       7 → same team      + same round preference
       4 → same dept      + same round preference
       2 → any dept       + same round preference
       1 → same dept      (round mismatch — tie-breaker only)
    """
    ai_result = _score_with_gemini(
        employees, target_dept, target_team, target_subteam, round_type
    )
    if ai_result:
        return ai_result

    # Fallback: improved rule-based scorer
    return _score_with_rules(
        employees, target_dept, target_team, target_subteam, round_type
    )

def _score_with_gemini(
    employees:      List[Dict],
    target_dept:    str,
    target_team:    str,
    target_subteam: Optional[str],
    round_type:     str,
) -> List[Dict]:
    """
    Ask Gemini to score every employee on a 0–10 scale for fitness
    as an interviewer for this specific position and round.
    Returns sorted list of {"employee": dict, "score": int, "match_reason": str}
    or empty list if Gemini unavailable/fails.
    """
    model = _get_gemini_model()
    if not model:
        return []

    # Build a lean employee list for the prompt (only relevant fields)
    emp_profiles = [
        {
            "id":          e["id"],
            "name":        e["name"],
            "department":  e["department"],
            "team":        e["team"],
            "subteam":     e.get("subteam") or "N/A",
            "designation": e["designation"],
            "round_pref":  e["round_type_preference"],
        }
        for e in employees
    ]

    prompt = f"""
You are an expert HR system for Nutrabay, a health supplement company.

Your task: Score each employee as a potential interviewer for the open position below.

OPEN POSITION:
- Department : {target_dept}
- Team       : {target_team}
- Subteam    : {target_subteam or "Any"}
- Round Type : {round_type}  (Technical = engineers assess skills | HR = HR team screens culture fit | Manager = senior leaders assess leadership)

SCORING RULES (strict):
1. Round-Department compatibility is the MOST important factor:
   - "HR" round       → ONLY HR department employees should score > 0. Others score 0.
   - "Manager" round  → Prefer Management dept, but senior leads from any dept are acceptable.
2. Within compatible employees, score based on:
   - Same subteam as position      : +4 bonus
   - Same team as position         : +3 bonus
   - Same department as position   : +2 bonus
   - Round preference matches      : +1 bonus
3. Final score out of 10 (0 = completely unsuitable, 10 = perfect match)
4. Never assign score > 0 to blocked departments (see rule 1)

EMPLOYEES TO SCORE:
{json.dumps(emp_profiles, indent=2)}

Return ONLY a valid JSON array, no explanation, no markdown. Each element:
{{"id": <employee_id>, "score": <0-10>, "reason": "<one short sentence why>"}}
""".strip()

    try:
        response = model.generate_content(prompt)
        raw = response.text.strip()
        # Strip markdown fences if present
        raw = re.sub(r"```(?:json)?", "", raw).strip("` \n")
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if not match:
            return []

        scored_raw = json.loads(match.group())

        # Map scores back to full employee dicts
        emp_by_id  = {e["id"]: e for e in employees}
        result     = []
        for item in scored_raw:
            emp_id = item.get("id")
            score  = int(item.get("score", 0))
            reason = item.get("reason", "AI matched")
            if emp_id in emp_by_id and score > 0:
                result.append({
                    "employee":     emp_by_id[emp_id],
                    "score":        score,
                    "match_reason": f"🤖 {reason}",
                })

        result.sort(key=lambda x: x["score"], reverse=True)
        return result

    except Exception as e:
        st.warning(f"Gemini scoring failed, using rule-based fallback: {e}")
        return []


# ────────────────────────────────────────────────────────────
# RULE-BASED FALLBACK — strict round-dept gating + hierarchy weights
# ────────────────────────────────────────────────────────────

ROUND_DEPT_RULES = {
    "HR": {
        "primary_depts":   ["HR"],
        "secondary_depts": ["Management"],
        "blocked_depts":   ["Technology", "Sales", "Marketing", "Finance", "Operations"],
    },
    "Technical": {
        "primary_depts":   ["Technology"],
        "secondary_depts": ["Operations", "Marketing"],   # e.g. marketing tech lead
        "blocked_depts":   ["HR", "Sales", "Finance"],
    },
    "Manager": {
        "primary_depts":   ["Management"],
        "secondary_depts": ["Technology", "HR", "Sales", "Marketing", "Finance"],
        "blocked_depts":   [],  # Managers can come from any dept
    },
}

def _score_with_rules(
    employees:      List[Dict],
    target_dept:    str,
    target_team:    str,
    target_subteam: Optional[str],
    round_type:     str,
) -> List[Dict]:
    """
    Improved rule-based scorer.
    Step 1 → Hard filter: block incompatible dept-round combos.
    Step 2 → Apply hierarchy weights within compatible employees only.
    """
    rules    = ROUND_DEPT_RULES.get(round_type, {})
    blocked  = set(rules.get("blocked_depts", []))
    primary  = set(rules.get("primary_depts", []))
    secondary= set(rules.get("secondary_depts", []))

    scored = []
    for emp in employees:
        dept_emp  = emp["department"]
        round_emp = emp["round_type_preference"]

        # ── Hard block: wrong department for this round ──────
        if dept_emp in blocked:
            continue

        # ── Round preference mismatch penalty ────────────────
        round_match = (round_emp == round_type)

        # ── Base score from dept tier ─────────────────────────
        if dept_emp in primary:
            base = 6 if round_match else 3
        elif dept_emp in secondary:
            base = 3 if round_match else 1
        else:
            # Manager round allows cross-dept, but lower base
            base = 2 if (round_type == "Manager" and round_match) else 0

        if base == 0:
            continue

        # ── Hierarchy bonus (only within compatible employees) ─
        bonus  = 0
        reason = f"{dept_emp} dept"

        if target_subteam and emp.get("subteam") == target_subteam and round_match:
            bonus  = 4
            reason = f"Same subteam ({target_subteam})"
        elif emp["team"] == target_team and round_match:
            bonus  = 3
            reason = f"Same team ({target_team})"
        elif dept_emp == target_dept and round_match:
            bonus  = 2
            reason = f"Same dept ({target_dept})"
        elif round_match:
            reason = f"{round_type} round pref"

        final_score = min(10, base + bonus)

        scored.append({
            "employee":     emp,
            "score":        final_score,
            "match_reason": f"📋 {reason}",
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ────────────────────────────────────────────────────────────
# ██  SCHEDULING ENGINE
# ────────────────────────────────────────────────────────────

def availability_to_datetimes(slots: List[Dict]) -> List[Tuple[datetime, datetime]]:
    """Convert DB availability rows to (start_dt, end_dt) tuples."""
    result = []
    for s in slots:
        try:
            start = datetime.strptime(f"{s['date']} {s['start_time']}", "%Y-%m-%d %H:%M")
            end   = datetime.strptime(f"{s['date']} {s['end_time']}",   "%Y-%m-%d %H:%M")
            if end > start:
                result.append((start, end))
        except ValueError:
            pass
    return result


def free_slots_to_datetimes(slots: List[Dict]) -> List[Tuple[datetime, datetime]]:
    """Same as above but for parsed candidate slots (date/start_time/end_time dicts)."""
    return availability_to_datetimes(slots)

def _normalize_slot(item) -> Optional[Tuple[datetime, datetime]]:
    """
    Accept either a (datetime, datetime) tuple OR a slot dict
    {date, start_time, end_time} and always return a (datetime, datetime) tuple.
    Returns None if conversion fails.
    """
    if isinstance(item, (tuple, list)):
        if len(item) == 2:
            return item[0], item[1]
        return None
    if isinstance(item, dict):
        try:
            start = datetime.strptime(
                f"{item['date']} {item['start_time']}", "%Y-%m-%d %H:%M"
            )
            end = datetime.strptime(
                f"{item['date']} {item['end_time']}", "%Y-%m-%d %H:%M"
            )
            if end > start:
                return start, end
        except (KeyError, ValueError):
            pass
    return None

def intersect_two_lists(
    list_a: list,
    list_b: list,
    min_minutes: int = 30,
) -> List[Tuple[datetime, datetime]]:
    """
    Find overlapping windows >= min_minutes.
    Handles both (datetime, datetime) tuples AND raw slot dicts defensively.
    """
    result = []
    for raw_a in list_a:
        pair_a = _normalize_slot(raw_a)
        if not pair_a:
            continue
        s1, e1 = pair_a

        for raw_b in list_b:
            pair_b = _normalize_slot(raw_b)
            if not pair_b:
                continue
            s2, e2 = pair_b

            overlap_s = max(s1, s2)
            overlap_e = min(e1, e2)
            if (overlap_e - overlap_s).total_seconds() >= min_minutes * 60:
                result.append((overlap_s, overlap_e))
    return result

def find_common_slots(
    all_slot_lists: list,
    min_minutes: int = 30,
) -> List[Tuple[datetime, datetime]]:
    """
    Iteratively intersect all participants' free-slot lists.
    Each inner list can contain raw dicts OR (datetime, datetime) tuples.
    Returns common windows sorted: longest first → earliest first.
    """
    if not all_slot_lists:
        return []

    # Normalise every inner list to (datetime, datetime) tuples upfront
    normalised = []
    for slot_list in all_slot_lists:
        converted = [_normalize_slot(s) for s in slot_list]
        cleaned   = [p for p in converted if p is not None]
        if cleaned:
            normalised.append(cleaned)

    if not normalised:
        return []

    running = normalised[0]
    for other in normalised[1:]:
        running = intersect_two_lists(running, other, min_minutes)
        if not running:
            break

    # Sort: longest duration first, then earliest start
    running.sort(key=lambda x: (-(x[1] - x[0]).total_seconds(), x[0]))
    return running


def build_slot_records(
    common: List[Tuple[datetime, datetime]],
    max_slots: int = 3,
    interview_duration_min: int = 60,
) -> List[Dict]:
    """
    Convert raw (start, end) pairs into enriched slot dicts.
    Caps the displayed slot at `interview_duration_min` minutes.
    Computes confidence score (60-95%) based on buffer size.
    """
    records = []
    for (start, end) in common[:max_slots]:
        total_min  = int((end - start).total_seconds() / 60)
        slot_min   = min(total_min, interview_duration_min)
        buffer_min = total_min - slot_min
        confidence = min(95, 60 + buffer_min // 5 + slot_min // 10)

        records.append({
            "date":       start.strftime("%Y-%m-%d"),
            "day":        start.strftime("%A"),
            "start_time": start.strftime("%H:%M"),
            "end_time":   (start + timedelta(minutes=slot_min)).strftime("%H:%M"),
            "duration":   slot_min,
            "buffer":     buffer_min,
            "confidence": confidence,
        })
    return records


def resolve_conflict_option_a(
    candidate_dt: List[Tuple[datetime, datetime]],
    interviewers_dt: List[List[Tuple[datetime, datetime]]],
    flex_minutes: int = 15,
) -> List[Tuple[datetime, datetime]]:
    """
    Conflict resolution Option A:
    Expand every interviewer's slots by ±flex_minutes and retry intersection.
    """
    expanded = []
    for slot_list in interviewers_dt:
        expanded.append([
            (s - timedelta(minutes=flex_minutes), e + timedelta(minutes=flex_minutes))
            for (s, e) in slot_list
        ])
    return find_common_slots([candidate_dt] + expanded, min_minutes=30)


def resolve_conflict_option_b(
    candidate_dt: List[Tuple[datetime, datetime]],
    all_scored:   List[Dict],
    current_interviewers: List[Dict],
    today_str: str,
) -> Tuple[List[Dict], List[Tuple[datetime, datetime]]]:
    """
    Conflict resolution Option B:
    Replace the lowest-scoring current interviewer with the next-best
    unused candidate from the full ranked list.
    Returns (new_interviewer_dicts, common_slots).
    """
    if not current_interviewers:
        return current_interviewers, []

    # Identify the lowest-scoring interviewer
    min_score    = min(i["score"] for i in current_interviewers)
    to_replace   = next(i for i in current_interviewers if i["score"] == min_score)
    current_ids  = {i["employee"]["id"] for i in current_interviewers}

    # Find next best replacement not already in panel
    replacement = next(
        (s for s in all_scored if s["employee"]["id"] not in current_ids),
        None
    )
    if not replacement:
        return current_interviewers, []

    new_panel = [i for i in current_interviewers if i != to_replace] + [replacement]

    # Fetch availability for new panel
    end_date = (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
    new_dt_lists = []
    for item in new_panel:
        slots = fetch_availability(item["employee"]["id"], today_str, end_date)
        new_dt_lists.append(availability_to_datetimes(slots))

    common = find_common_slots([candidate_dt] + new_dt_lists)
    return new_panel, common


# ────────────────────────────────────────────────────────────
# ██  SHARED UI HELPERS
# ────────────────────────────────────────────────────────────

def nb_header(title: str, subtitle: str = "") -> None:
    st.markdown(
        f'<div class="nb-header"><h1>{title}</h1>'
        + (f'<p>{subtitle}</p>' if subtitle else '')
        + '</div>',
        unsafe_allow_html=True,
    )


def interviewer_card(item: Dict, col) -> None:
    emp   = item["employee"]
    score = item["score"]
    color = (NUTRABAY_GREEN if score >= 7
             else "#F39C12" if score >= 4
             else "#95A5A6")
    with col:
        st.markdown(f"""
        <div style="border:1.5px solid {color}; border-radius:10px; padding:14px;
                    background:{color}10; text-align:center;">
            <b style="font-size:0.95rem;">{emp['name']}</b><br>
            <small style="color:#555;">{emp['designation']}</small><br>
            <small style="color:#777;">{emp['team']} › {emp.get('subteam') or '—'}</small><br>
            <span style="color:{color}; font-weight:700; font-size:0.85rem;">
                ⭐ {score}/10 — {item['match_reason']}
            </span>
        </div>
        """, unsafe_allow_html=True)


def slot_card(rank: int, slot: Dict, reasoning: str) -> None:
    medal = ["🥇", "🥈", "🥉"][rank] if rank < 3 else f"#{rank+1}"
    conf_color = (NUTRABAY_GREEN if slot["confidence"] >= 80
                  else "#F39C12" if slot["confidence"] >= 60
                  else "#E74C3C")
    st.markdown(f"""
    <div class="slot-card">
        <span style="font-size:1.2rem;">{medal}</span>
        <strong style="font-size:1.05rem; margin-left:8px;">
            {slot['day']}, {slot['date']}
        </strong>
        &nbsp;&nbsp;
        <span style="font-size:0.95rem;">
            ⏰ {slot['start_time']} – {slot['end_time']}
            &nbsp;({slot['duration']} min)
        </span>
        &nbsp;&nbsp;
        <span class="conf-pill" style="color:{conf_color}; border-color:{conf_color};">
            {slot['confidence']}% match
        </span>
        <br>
        <small style="color:#555; margin-top:6px; display:block;">💡 {reasoning}</small>
    </div>
    """, unsafe_allow_html=True)


def status_badge(status: str) -> str:
    colors = {
        "Scheduled": ("#27AE60", "🟢"),
        "Pending":   ("#F39C12", "🟡"),
        "Cancelled": ("#E74C3C", "🔴"),
        "Completed": ("#3498DB", "🔵"),
    }
    c, e = colors.get(status, ("#999", "⚪"))
    return f'<span style="color:{c};font-weight:600;">{e} {status}</span>'


# ────────────────────────────────────────────────────────────
# ██  PAGE 1 — CANDIDATE INTERVIEW REQUEST
# ────────────────────────────────────────────────────────────

def page_schedule_interview() -> None:
    nb_header(
        "🥗 Nutrabay Interview Scheduler",
        "Submit your availability — our AI will find the perfect interview slot."
    )

    all_emps = fetch_all_employees()

    # ────────────────────────────────────────────────────────
    # SECTION A — Position + Round (ALL outside form)
    # These drive both the live preview AND the final matching.
    # round_type lives here so it's shared — no duplication.
    # ────────────────────────────────────────────────────────
    st.subheader("① Position & Round")
    st.caption("All four selectors update the interviewer preview in real time.")

    s1, s2, s3, s4 = st.columns(4)

    depts = sorted({e["department"] for e in all_emps})
    with s1:
        dept = st.selectbox("Department *", depts, key="sel_dept")

    teams = sorted({e["team"] for e in all_emps if e["department"] == dept})
    with s2:
        team = st.selectbox(
            "Team *",
            options=teams if teams else ["—"],
            key="sel_team",
        )

    subteams = sorted({
        e["subteam"] for e in all_emps
        if e["team"] == team and e.get("subteam")
    })
    with s3:
        subteam_raw = st.selectbox(
            "Subteam", ["(Any)"] + subteams, key="sel_subteam"
        )

    with s4:
        # round_type is now OUTSIDE the form — shared across preview + submit
        round_type = st.selectbox("Interview Round *", ROUNDS, key="sel_round")

    target_subteam = None if subteam_raw == "(Any)" else subteam_raw

    # ────────────────────────────────────────────────────────
    # LIVE PREVIEW — uses cached AI result, no re-run on refresh
    # ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("👥 Matched Interview Panel")

    cache_key     = f"scored__{dept}__{team}__{target_subteam or 'any'}__{round_type}"
    already_cached = cache_key in st.session_state

    scored_all = get_scored_interviewers_cached(dept, team, target_subteam, round_type)

    if already_cached:
        st.caption("⚡ Loaded from cache — change any selector above to re-score.")
    else:
        st.caption("✅ AI scored interviewers for this combination.")

    if scored_all:
        top4  = scored_all[:4]
        cols  = st.columns(len(top4))
        for idx, item in enumerate(top4):
            interviewer_card(item, cols[idx])

        # Show full ranked list in expander
        with st.expander(f"📊 Full ranked list ({len(scored_all)} candidates)", expanded=False):
            ranked_data = [
                {
                    "Rank":        i + 1,
                    "Name":        item["employee"]["name"],
                    "Designation": item["employee"]["designation"],
                    "Department":  item["employee"]["department"],
                    "Team":        item["employee"]["team"],
                    "Subteam":     item["employee"].get("subteam") or "—",
                    "Round Pref":  item["employee"]["round_type_preference"],
                    "Score":       item["score"],
                    "Reason":      item["match_reason"],
                }
                for i, item in enumerate(scored_all)
            ]
            st.dataframe(
                pd.DataFrame(ranked_data),
                use_container_width=True,
                hide_index=True,
            )

        # Button to force re-score (clears cache for this combo)
        if st.button("🔄 Re-run AI Scoring", key="rescore_btn",
                     help="Force Gemini to re-score this combination"):
            if cache_key in st.session_state:
                del st.session_state[cache_key]
            st.rerun()
    else:
        st.warning(
            f"No suitable interviewers found for **{dept} › {team}** "
            f"with **{round_type}** round. Add employees via the Admin panel."
        )
        return

    # ────────────────────────────────────────────────────────
    # SECTION B — Candidate details + Availability (inside form)
    # round_type is already captured above — not repeated here.
    # ────────────────────────────────────────────────────────
    st.divider()
    with st.form("candidate_form", clear_on_submit=False):
        st.subheader("② Candidate Details")
        c1, c2 = st.columns(2)
        with c1:
            cname  = st.text_input("Full Name *",  placeholder="Ravi Shankar")
        with c2:
            cemail = st.text_input("Email *", placeholder="ravi@example.com")

        st.subheader("③ Your Availability")
        tab_nl, tab_str = st.tabs([
            "✍️ Natural Language (recommended)", "🗓️ Structured Picker"
        ])

        with tab_nl:
            nl_text = st.text_area(
                "Describe when you're free",
                placeholder=(
                    'e.g. "Tue–Thu 2–5 PM, Fri 9 AM–12 PM next week"\n'
                    'or   "Monday 10 AM to 1 PM, Wednesday 3–6 PM this week"'
                ),
                height=90,
            )

        with tab_str:
            st.info("Fill up to 4 slots. Leave date blank to skip a row.")
            struct_slots = []
            for i in range(4):
                sc1, sc2, sc3 = st.columns(3)
                with sc1:
                    sd = st.date_input(
                        f"Date {i+1}", value=None,
                        min_value=date.today(), key=f"sd{i}"
                    )
                with sc2:
                    ss = st.time_input(f"Start {i+1}", value=time(9, 0),  key=f"ss{i}")
                with sc3:
                    se = st.time_input(f"End {i+1}",   value=time(11, 0), key=f"se{i}")
                if sd:
                    struct_slots.append({
                        "date":       sd.strftime("%Y-%m-%d"),
                        "start_time": ss.strftime("%H:%M"),
                        "end_time":   se.strftime("%H:%M"),
                    })

        submitted = st.form_submit_button(
            "🚀 Find Best Interview Slots", use_container_width=True
        )

    # ── Process on submit ───────────────────────────────────
    if not submitted:
        return

    # Read position values from session_state (set by outside-form widgets)
    dept           = st.session_state.get("sel_dept",    dept)
    team           = st.session_state.get("sel_team",    team)
    subteam_raw    = st.session_state.get("sel_subteam", subteam_raw)
    round_type     = st.session_state.get("sel_round",   round_type)
    target_subteam = None if subteam_raw == "(Any)" else subteam_raw

    # Validation
    if not cname.strip() or not cemail.strip():
        st.error("⚠️ Please provide candidate name and email.")
        return
    if not re.match(r"[^@]+@[^@]+\.[^@]+", cemail):
        st.error("⚠️ Please enter a valid email address.")
        return
    if team == "—":
        st.error("⚠️ Please select a valid department with teams configured.")
        return

    # ── Re-use CACHED scores — no second Gemini call ────────
    scored_all = get_scored_interviewers_cached(dept, team, target_subteam, round_type)
    top4       = scored_all[:4]

    st.caption("✅ Using cached interviewer scores — no extra AI call needed.")

    # ── Parse candidate availability ───────────────────────
    candidate_slots: List[Dict] = []
    if nl_text.strip():
        with st.spinner("🤖 Parsing availability…"):
            candidate_slots = gemini_parse_availability(nl_text)
        if candidate_slots:
            st.success(f"✅ Parsed **{len(candidate_slots)}** slot(s) from your text.")
        else:
            st.warning("Could not parse text — using structured slots if provided.")
    candidate_slots.extend(struct_slots)

    if not candidate_slots:
        st.error("No availability slots detected. Please add slots and try again.")
        return

    with st.expander("📋 Parsed Candidate Slots", expanded=False):
        st.dataframe(
            pd.DataFrame(candidate_slots),
            use_container_width=True,
            hide_index=True,
        )

    # ── Fetch interviewer availability & find common slots ──
    today_str  = date.today().strftime("%Y-%m-%d")
    end_str    = (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
    cand_dt    = free_slots_to_datetimes(candidate_slots)

    iv_dt_list = []
    for item in top4:
        rows = fetch_availability(item["employee"]["id"], today_str, end_str)
        iv_dt_list.append(availability_to_datetimes(rows))

    common = find_common_slots([cand_dt] + iv_dt_list)

    st.divider()
    st.subheader("🎯 Top Interview Slot Recommendations")

    if common:
        top_slots = build_slot_records(common, max_slots=3)
        for i, slot in enumerate(top_slots):
            with st.spinner(f"💬 Generating reasoning for slot {i+1}…"):
                reasoning = gemini_generate_reasoning(
                    slot,
                    [item["employee"] for item in top4],
                    cname,
                    f"{dept} › {team}",
                )
            slot_card(i, slot, reasoning)
            if st.button(f"✅ Confirm & Book Slot {i+1}", key=f"book_{i}"):
                iid = create_interview(
                    cname, cemail, round_type, dept, team,
                    target_subteam or "",
                    [item["employee"] for item in top4], slot, reasoning
                )
                st.success(f"🎉 Scheduled! Reference ID: **#{iid}**")
                st.balloons()
    else:
        st.warning("⚠️ No perfect overlap found. Running conflict resolution…")
        cr1, cr2 = st.columns(2)

        with cr1:
            st.markdown(f"""
            <div style="background:#FFF9E6; border:1px solid #F39C12;
                        border-radius:8px; padding:14px;">
                <b>🔧 Option A — Same Panel, ±15 min Flexibility</b><br>
                <small>Slightly expands each window to find overlap.</small>
            </div>
            """, unsafe_allow_html=True)
            st.write("")
            flex_common = resolve_conflict_option_a(cand_dt, iv_dt_list)
            if flex_common:
                fs = build_slot_records(flex_common, 1)
                if fs:
                    s = fs[0]
                    st.success(f"**{s['day']}, {s['date']}** · {s['start_time']}–{s['end_time']}")
                    if st.button("📅 Book with Flexibility", key="book_flex"):
                        create_interview(
                            cname, cemail, round_type, dept, team,
                            target_subteam or "",
                            [i["employee"] for i in top4], s,
                            "Scheduled with ±15 min panel flexibility."
                        )
                        st.success("✅ Booked!")
            else:
                st.error("No flexible overlap found.")

        with cr2:
            st.markdown(f"""
            <div style="background:#EFF8FF; border:1px solid #3498DB;
                        border-radius:8px; padding:14px;">
                <b>🔄 Option B — Swap Lowest-Scored Interviewer</b><br>
                <small>Replaces weakest panel member with next best match.</small>
            </div>
            """, unsafe_allow_html=True)
            st.write("")
            new_panel, new_common = resolve_conflict_option_b(
                cand_dt, scored_all, top4, today_str
            )
            replaced_name = new_panel[-1]["employee"]["name"] if new_panel else None
            if new_common and replaced_name:
                ns = build_slot_records(new_common, 1)
                if ns:
                    s = ns[0]
                    st.success(
                        f"Replacing with **{replaced_name}**:\n"
                        f"**{s['day']}, {s['date']}** · {s['start_time']}–{s['end_time']}"
                    )
                    if st.button("👥 Book with Replacement", key="book_replace"):
                        create_interview(
                            cname, cemail, round_type, dept, team,
                            target_subteam or "",
                            [i["employee"] for i in new_panel], s,
                            f"Scheduled after replacing with {replaced_name}."
                        )
                        st.success("✅ Booked with updated panel!")
            else:
                st.error("No overlap even after replacement.")

# ────────────────────────────────────────────────────────────
# ██  PAGE 2 — INTERVIEWER AVAILABILITY MANAGER
# ────────────────────────────────────────────────────────────

def page_availability_manager() -> None:
    nb_header("📅 Availability Manager", "Employees: log your free windows for interviews")

    all_emps = fetch_all_employees()
    emp_map  = {e["id"]: f"{e['name']}  ({e['designation']} · {e['team']})" for e in all_emps}

    left, right = st.columns([1, 2])


    with left:
        st.subheader("Select Employee")
        sel_id = st.selectbox(
            "Employee", options=list(emp_map.keys()),
            format_func=lambda x: emp_map[x],
        )
        emp = fetch_employee(sel_id)
        if emp:
            st.markdown(f"""
            <div style="background:{NUTRABAY_BG}; border:1px solid {NUTRABAY_GREEN};
                        border-radius:8px; padding:12px; margin-top:8px;">
                <b>👤 {emp['name']}</b><br>
                📧 <small>{emp['email']}</small><br>
                🏢 {emp['department']} › {emp['team']}<br>
                🏷️ Subteam: {emp.get('subteam') or '—'}<br>
                🔖 Prefers: <b>{emp['round_type_preference']}</b> round
            </div>
            """, unsafe_allow_html=True)

    with right:
        st.subheader("➕ Add Free Slots")
        tab_manual, tab_nl, tab_copy = st.tabs([
            "📆 Manual Entry", "🤖 Natural Language", "📋 Copy Previous Week"
        ])

        # ── Manual entry ────────────────────────────────────
        with tab_manual:
            with st.form("avail_manual", clear_on_submit=True):
                mc1, mc2, mc3 = st.columns(3)
                with mc1:
                    av_date  = st.date_input("Date *", min_value=date.today())
                with mc2:
                    av_start = st.time_input("Start *", value=time(9, 0))
                with mc3:
                    av_end   = st.time_input("End *",   value=time(11, 0))
                if st.form_submit_button("Add Slot", use_container_width=True):
                    if av_start >= av_end:
                        st.error("End time must be after start time.")
                    else:
                        upsert_availability(
                            sel_id,
                            av_date.strftime("%Y-%m-%d"),
                            av_start.strftime("%H:%M"),
                            av_end.strftime("%H:%M"),
                        )
                        st.success("✅ Slot added!")
                        st.rerun()

        # ── Natural language ─────────────────────────────────
        with tab_nl:
            nl_input = st.text_area(
                "Describe your free time",
                placeholder='e.g. "Mon 9-11 AM, Wed 2-5 PM, Thu 10 AM–1 PM"',
                height=80, key="nl_avail"
            )
            if st.button("🤖 Parse & Add Slots", key="parse_nl_avail", use_container_width=True):
                if nl_input.strip():
                    parsed = gemini_parse_availability(nl_input)
                    if parsed:
                        for s in parsed:
                            upsert_availability(sel_id, s["date"], s["start_time"], s["end_time"])
                        st.success(f"✅ Added {len(parsed)} parsed slot(s)!")
                        st.rerun()
                    else:
                        st.error("Could not parse. Try rephrasing or use manual entry.")

        # ── Copy previous week ───────────────────────────────
        with tab_copy:
            st.info(
                "Copies all your slots from the previous 7 days, shifted forward by 7 days "
                "(skips weekends automatically)."
            )
            if st.button("📋 Copy Last Week's Slots", use_container_width=True, key="copy_week"):
                prev_end   = date.today().strftime("%Y-%m-%d")
                prev_start = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
                past_slots = fetch_availability(sel_id, prev_start, prev_end)
                if past_slots:
                    count = 0
                    for s in past_slots:
                        new_d = datetime.strptime(s["date"], "%Y-%m-%d") + timedelta(days=7)
                        if new_d.date().weekday() < 5:     # skip weekends
                            upsert_availability(
                                sel_id,
                                new_d.strftime("%Y-%m-%d"),
                                s["start_time"],
                                s["end_time"],
                            )
                            count += 1
                    st.success(f"✅ Copied {count} slot(s) to next week!")
                    st.rerun()
                else:
                    st.info("No slots from last week to copy.")

    # ── Display current availability ────────────────────────
    st.divider()
    st.subheader(f"📋 Upcoming Free Slots — {emp_map[sel_id]}")
    today_s = date.today().strftime("%Y-%m-%d")
    end_s   = (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")
    slots   = fetch_availability(sel_id, today_s, end_s)

    if not slots:
        st.info("No availability logged for the next 2 weeks. Add slots above.")
        return

    df = pd.DataFrame(slots)
    df["day"]       = pd.to_datetime(df["date"]).dt.strftime("%A")
    df["duration"]  = df.apply(
        lambda r: int((datetime.strptime(r["end_time"], "%H:%M") -
                        datetime.strptime(r["start_time"], "%H:%M")).seconds / 60), axis=1
    )
    df = df.sort_values(["date", "start_time"]).reset_index(drop=True)

    for _, row in df.iterrows():
        c1, c2, c3, c4 = st.columns(4)
        with c1: st.write(f"📅 **{row['day']}**, {row['date']}")
        with c2: st.write(f"⏰ {row['start_time']} – {row['end_time']}")
        with c3: st.write(f"⏱️ {row['duration']} min")
        with c4:
            if st.button("🗑️", key=f"del_{row['id']}", help="Remove this slot"):
                remove_availability(int(row["id"]))
                st.rerun()


# ────────────────────────────────────────────────────────────
# ██  PAGE 3 — HR DASHBOARD
# ────────────────────────────────────────────────────────────

def page_hr_dashboard() -> None:
    nb_header("📊 HR Dashboard", "Monitor, reschedule, and manage all interview slots")

    interviews = fetch_all_interviews()

    # ── KPI metrics ─────────────────────────────────────────
    total     = len(interviews)
    scheduled = sum(1 for i in interviews if i["status"] == "Scheduled")
    pending   = sum(1 for i in interviews if i["status"] == "Pending")
    cancelled = sum(1 for i in interviews if i["status"] == "Cancelled")
    completed = sum(1 for i in interviews if i["status"] == "Completed")

    k1, k2, k3, k4, k5 = st.columns(5)
    for col, label, val, color in [
        (k1, "Total",     total,     "#555"),
        (k2, "Scheduled", scheduled, NUTRABAY_GREEN),
        (k3, "Pending",   pending,   "#F39C12"),
        (k4, "Completed", completed, "#3498DB"),
        (k5, "Cancelled", cancelled, "#E74C3C"),
    ]:
        with col:
            st.markdown(f"""
            <div class="metric-box" style="border-left-color:{color};">
                <div class="val" style="color:{color};">{val}</div>
                <div class="lbl">{label}</div>
            </div>
            """, unsafe_allow_html=True)

    if not interviews:
        st.info("No interviews yet. They will appear here once candidates submit requests.")
        return

    st.divider()

    # ── Filters ─────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        status_filter = st.multiselect(
            "Status", ["Scheduled", "Pending", "Cancelled", "Completed"],
            default=["Scheduled", "Pending"]
        )
    with fc2:
        round_filter = st.selectbox("Round", ["All"] + ROUNDS)
    with fc3:
        dept_filter = st.selectbox(
            "Department", ["All"] + sorted({i["position_department"] for i in interviews})
        )

    filtered = [
        i for i in interviews
        if (not status_filter or i["status"] in status_filter)
        and (round_filter == "All" or i["round_type"] == round_filter)
        and (dept_filter  == "All" or i["position_department"] == dept_filter)
    ]

    st.markdown(f"**{len(filtered)}** interview(s) matching filters")

    # ── Interview rows ───────────────────────────────────────
    for iv in filtered:
        slot         = json.loads(iv["scheduled_slot"]) if iv["scheduled_slot"] else {}
        interviewers = json.loads(iv["selected_interviewers"]) if iv["selected_interviewers"] else []

        slot_str = (f"{slot.get('date','?')} · {slot.get('start_time','?')}–{slot.get('end_time','?')}"
                    if slot else "TBD")

        with st.expander(
            f"#{iv['id']}  |  {iv['candidate_name']}  |  "
            f"{iv['round_type']} Round  |  {slot_str}  |  {iv['status']}"
        ):
            d1, d2 = st.columns(2)
            with d1:
                st.markdown(f"""
                **Candidate:** {iv['candidate_name']}  
                **Email:** {iv['candidate_email']}  
                **Round:** {iv['round_type']}  
                **Position:** {iv['position_department']} › {iv['position_team']}
                  › {iv.get('position_subteam') or '—'}  
                **Created:** {iv['created_at']}
                """)
            with d2:
                st.markdown(
                    f"**Status:** {status_badge(iv['status'])}",
                    unsafe_allow_html=True
                )
                if slot:
                    st.markdown(f"""
                    **Date:** {slot.get('date')}  
                    **Time:** {slot.get('start_time')} – {slot.get('end_time')}  
                    **Duration:** {slot.get('duration', '?')} min
                    """)

            # Interviewers row
            if interviewers:
                st.markdown("**Interview Panel:**")
                iv_cols = st.columns(min(len(interviewers), 5))
                for j, iv_emp in enumerate(interviewers[:5]):
                    with iv_cols[j]:
                        st.markdown(f"""
                        <div style="border:1px solid {NUTRABAY_GREEN}; border-radius:8px;
                                    padding:8px; text-align:center; font-size:0.8rem;
                                    background:{NUTRABAY_BG};">
                            👤 <b>{iv_emp.get('name','?')}</b><br>
                            {iv_emp.get('designation','')}<br>
                            <small>{iv_emp.get('team','')}</small>
                        </div>
                        """, unsafe_allow_html=True)

            if iv.get("reasoning"):
                st.info(f"💡 **Reasoning:** {iv['reasoning']}")

            # ── Action buttons ─────────────────────────────
            if iv["status"] not in ("Cancelled", "Completed"):
                st.markdown("---")
                a1, a2, a3 = st.columns(3)
                with a1:
                    if st.button("✅ Complete", key=f"cmp_{iv['id']}"):
                        patch_interview(iv["id"], "Completed")
                        print(f"[EMAIL] Completion notice sent to {iv['candidate_email']}")
                        st.success("Marked complete.")
                        st.rerun()
                with a2:
                    if st.button("🔄 Reschedule", key=f"rsch_{iv['id']}"):
                        st.session_state[f"rsch_{iv['id']}"] = True
                with a3:
                    if st.button("❌ Cancel", key=f"cncl_{iv['id']}"):
                        patch_interview(iv["id"], "Cancelled")
                        print(f"[EMAIL] Cancellation notice sent to {iv['candidate_email']}")
                        st.error("Interview cancelled.")
                        st.rerun()

                # ── Reschedule modal ───────────────────────
                if st.session_state.get(f"rsch_{iv['id']}", False):
                    st.markdown("#### 🔄 Reschedule Options")
                    ro1, ro2 = st.columns(2)
                    today_s = date.today().strftime("%Y-%m-%d")
                    end_s   = (date.today() + timedelta(days=14)).strftime("%Y-%m-%d")

                    with ro1:
                        st.markdown("**Option 1:** Keep same panel → find next available slot")
                        if st.button("Find Next Slot →", key=f"nxt_{iv['id']}"):
                            iv_ids = [e.get("id") for e in interviewers if e.get("id")]
                            dt_lists = [
                                availability_to_datetimes(fetch_availability(eid, today_s, end_s))
                                for eid in iv_ids
                            ]
                            new_common = find_common_slots([s for s in dt_lists if s])
                            if new_common:
                                ns = build_slot_records(new_common, 1)
                                patch_interview(
                                    iv["id"], "Scheduled", ns, interviewers,
                                    "Rescheduled with original panel."
                                )
                                st.success(f"Rescheduled to {ns['date']} {ns['start_time']}")
                                st.session_state[f"rsch_{iv['id']}"] = False
                                st.rerun()
                            else:
                                st.error("No common slot for current panel.")

                    with ro2:
                        st.markdown("**Option 2:** Keep same slot → replace a panellist")
                        if st.button("Swap Panellist →", key=f"swp_{iv['id']}"):
                            all_emp    = fetch_all_employees()
                            scored_all = score_and_rank_interviewers(
                                all_emp,
                                iv["position_department"],
                                iv["position_team"],
                                iv.get("position_subteam"),
                                iv["round_type"],
                            )
                            used_ids = {e.get("id") for e in interviewers}
                            replacement = next(
                                (s for s in scored_all
                                 if s["employee"]["id"] not in used_ids), None
                            )
                            if replacement:
                                new_iv = interviewers[:-1] + [replacement["employee"]]
                                patch_interview(
                                    iv["id"], "Scheduled", slot, new_iv,
                                    f"Panel updated: replaced with {replacement['employee']['name']}."
                                )
                                st.success(f"Swapped in {replacement['employee']['name']}.")
                                st.session_state[f"rsch_{iv['id']}"] = False
                                st.rerun()
                            else:
                                st.error("No suitable replacement available.")

                    if st.button("✖ Close", key=f"close_rsch_{iv['id']}"):
                        st.session_state[f"rsch_{iv['id']}"] = False
                        st.rerun()


# ────────────────────────────────────────────────────────────
# ██  PAGE 4 — ADMIN PANEL
# ────────────────────────────────────────────────────────────

def page_admin() -> None:
    nb_header("⚙️ Admin — Employee Database", "Add, view, and remove interviewers")

    tab_view, tab_add = st.tabs(["👥 All Employees", "➕ Add Employee"])

    # ── View & delete ────────────────────────────────────────
    with tab_view:
        all_emps = fetch_all_employees()

        f1, f2 = st.columns(2)
        with f1:
            d_filter = st.selectbox("Filter Dept", ["All"] + sorted({e["department"] for e in all_emps}))
        with f2:
            r_filter = st.selectbox("Filter Round", ["All"] + ROUNDS)

        shown = [
            e for e in all_emps
            if (d_filter == "All" or e["department"] == d_filter)
            and (r_filter == "All" or e["round_type_preference"] == r_filter)
        ]

        st.markdown(f"**{len(shown)}** employee(s)")

        if shown:
            df = pd.DataFrame(shown)[[
                "id", "name", "email", "department", "team",
                "subteam", "designation", "round_type_preference"
            ]].rename(columns={
                "id":                    "ID",
                "name":                  "Name",
                "email":                 "Email",
                "department":            "Dept",
                "team":                  "Team",
                "subteam":               "Subteam",
                "designation":           "Designation",
                "round_type_preference": "Round Pref",
            })
            st.dataframe(df, use_container_width=True, hide_index=True)

            st.subheader("🗑️ Remove Employee")
            del_id = st.selectbox(
                "Select to remove",
                [None] + [e["id"] for e in shown],
                format_func=lambda x: "— Select —" if x is None
                                      else f"{next(e['name'] for e in shown if e['id']==x)} (#{x})"
            )
            if del_id and st.button("🗑️ Delete (removes all their availability too)", type="primary"):
                remove_employee_db(del_id)
                st.success("Employee removed.")
                st.rerun()

    # ── Add employee ─────────────────────────────────────────
    with tab_add:
        with st.form("add_emp_form", clear_on_submit=True):
            ac1, ac2 = st.columns(2)
            with ac1:
                n_name   = st.text_input("Full Name *")
                n_email  = st.text_input("Email *")
                n_dept   = st.selectbox("Department *", DEPARTMENTS)
                n_round  = st.selectbox("Round Preference *", ROUNDS)
            with ac2:
                n_desig  = st.text_input("Designation *", placeholder="e.g. Senior ML Engineer")
                n_team   = st.text_input("Team *",        placeholder="e.g. AI, Backend, Frontend")
                n_sub    = st.text_input("Subteam",       placeholder="e.g. AI-ML, DevOps (optional)")

            if st.form_submit_button("➕ Add Employee", use_container_width=True):
                if not all([n_name.strip(), n_email.strip(), n_desig.strip(), n_team.strip()]):
                    st.error("Please fill all required (*) fields.")
                elif not re.match(r"[^@]+@[^@]+\.[^@]+", n_email):
                    st.error("Invalid email address.")
                else:
                    ok = add_employee_db(
                        n_name.strip(), n_email.strip(), n_dept,
                        n_team.strip(), n_sub.strip(), n_desig.strip(), n_round
                    )
                    if ok:
                        st.success(f"✅ {n_name} added successfully!")
                        st.rerun()
                    else:
                        st.error("An employee with that email already exists.")


# ────────────────────────────────────────────────────────────
# ██  SIDEBAR + MAIN ROUTER
# ────────────────────────────────────────────────────────────

def ensure_db() -> None:
    """Auto-initialise DB if it doesn't exist."""
    if not os.path.exists(DB_PATH):
        from database_init import init_db
        init_db()


def main() -> None:
    ensure_db()

    with st.sidebar:
        # Logo / branding
        st.markdown(f"""
        <div style="text-align:center; padding:10px 0 6px;">
            <div style="background:linear-gradient(135deg,{NUTRABAY_DARK},{NUTRABAY_GREEN});
                        color:white; border-radius:10px; padding:14px; font-size:1.3rem;
                        font-weight:800; letter-spacing:1px;">
                🥗 NUTRABAY
            </div>
            <small style="color:#888;">Interview Scheduler MVP v1.0</small>
        </div>
        """, unsafe_allow_html=True)

        st.divider()

        # ── Gemini API key ───────────────────────────────────
        gemini_status = "✅ Gemini AI active" if _GEMINI_API_KEY else "⚠️ Gemini not configured"
        gemini_color  = NUTRABAY_GREEN if _GEMINI_API_KEY else "#F39C12"
        st.markdown(
            f'<div style="background:{gemini_color}18; border:1px solid {gemini_color}; '
            f'border-radius:8px; padding:10px; font-size:0.85rem; color:{gemini_color}; '
            f'font-weight:600; text-align:center;">{gemini_status}</div>',
            unsafe_allow_html=True
        )
        st.divider()

        # ── Navigation ───────────────────────────────────────
        st.subheader("📌 Navigation")
        page = st.radio(
            "page",
            options=[
                "🎯 Schedule Interview",
                "📅 Manage Availability",
                "📊 HR Dashboard",
                "⚙️ Admin Panel",
            ],
            label_visibility="collapsed",
        )

        st.divider()
        st.markdown("""
        <small style="color:#999;">
        Built for Nutrabay · Powered by<br>Streamlit + Google Gemini AI<br>
        © 2025 Nutrabay Technologies
        </small>
        """, unsafe_allow_html=True)

    # Route to selected page
    if page == "🎯 Schedule Interview":
        page_schedule_interview()
    elif page == "📅 Manage Availability":
        page_availability_manager()
    elif page == "📊 HR Dashboard":
        page_hr_dashboard()
    elif page == "⚙️ Admin Panel":
        page_admin()


if __name__ == "__main__":
    main()
