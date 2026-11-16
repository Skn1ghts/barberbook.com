import hmac
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from datetime import timedelta
from datetime import time as dtime
from pathlib import Path
from functools import wraps

from werkzeug.security import generate_password_hash, check_password_hash

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, session, jsonify
)

app = Flask(__name__)
app.secret_key = "barberbook-local-demo-change-this-key"

# Every page the site needs. If one is missing, say so clearly at startup.
REQUIRED_TEMPLATES = ["index.html", "auth.html", "login.html", "admin.html", "barber.html"]


def check_templates():
    folder = Path(app.root_path) / app.template_folder
    missing = [name for name in REQUIRED_TEMPLATES if not (folder / name).exists()]
    print("Templates folder:", folder)
    if missing:
        print("!!! MISSING TEMPLATES:", ", ".join(missing))
        print("!!! Put these files into the folder above, then restart.")


check_templates()

BASE_DIR = Path(__file__).resolve().parent
# Both can be overridden with environment variables (useful for testing
# against a copy of the database); by default they live next to app.py.
DB_PATH = Path(os.environ.get("BARBERBOOK_DB", BASE_DIR / "barbershop.sqlite"))
BACKUP_DIR = Path(os.environ.get("BARBERBOOK_BACKUP_DIR", BASE_DIR / "backups"))
MAX_BACKUPS = 7


# --------------------------------------------------
# DATABASE CONNECTION
# --------------------------------------------------

def get_db():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# --------------------------------------------------
# DATABASE MIGRATION
# Adds the login columns to an existing database.
# Safe to run on every start: it only changes what is missing.
# --------------------------------------------------

def migrate_db():
    if not DB_PATH.exists():
        return

    conn = sqlite3.connect(DB_PATH, timeout=10)
    try:
        def columns(table):
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

        customer_cols = columns("Customers")
        for col in ("first_name", "last_name", "password_hash"):
            if col not in customer_cols:
                conn.execute(f"ALTER TABLE Customers ADD COLUMN {col} TEXT")

        barber_cols = columns("Barbers")
        names_missing = "first_name" not in barber_cols
        for col in ("first_name", "last_name", "password_hash"):
            if col not in barber_cols:
                conn.execute(f"ALTER TABLE Barbers ADD COLUMN {col} TEXT")

        # Existing barbers ("Arman S.") get first/last name from full_name.
        # They have NO password until the admin sets one.
        if names_missing:
            for barber_id, full_name in conn.execute(
                "SELECT barber_id, full_name FROM Barbers"
            ).fetchall():
                parts = full_name.split(None, 1)
                first = parts[0] if parts else ""
                last = parts[1].strip(". ") if len(parts) > 1 else ""
                conn.execute(
                    "UPDATE Barbers SET first_name = ?, last_name = ? "
                    "WHERE barber_id = ?",
                    (first, last, barber_id)
                )

        # Appointments: group id that ties together the hourly rows of one break
        if "break_group" not in columns("Appointments"):
            conn.execute("ALTER TABLE Appointments ADD COLUMN break_group TEXT")

        # Appointments: total length of a booking with several services.
        # NULL (old rows, breaks) = use the length of the main service.
        if "duration_minutes" not in columns("Appointments"):
            conn.execute("ALTER TABLE Appointments ADD COLUMN duration_minutes INTEGER")

        # All services of one booking (Appointments.service_id stays the first one)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS AppointmentServices (
                appointment_id INTEGER NOT NULL,
                service_id INTEGER NOT NULL,
                PRIMARY KEY (appointment_id, service_id),
                FOREIGN KEY (appointment_id) REFERENCES Appointments(appointment_id) ON DELETE CASCADE,
                FOREIGN KEY (service_id) REFERENCES Services(service_id) ON DELETE CASCADE
            )
        """)

        # Barber ratings (1-5 stars, one per client per barber)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS BarberRatings (
                customer_id INTEGER NOT NULL,
                barber_id INTEGER NOT NULL,
                rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                updated_at TEXT NOT NULL,
                PRIMARY KEY (customer_id, barber_id),
                FOREIGN KEY (customer_id) REFERENCES Customers(customer_id) ON DELETE CASCADE,
                FOREIGN KEY (barber_id) REFERENCES Barbers(barber_id) ON DELETE CASCADE
            )
        """)

        conn.commit()
    finally:
        conn.close()


migrate_db()


# --------------------------------------------------
# VALIDATION HELPERS
# --------------------------------------------------

# Phone used by the hidden "Admin - Break" customer. Nobody may register it.
BREAK_PHONE = "0000000000"


def validate_name(value, label):
    """Letters only (any language). Returns an error text or None."""
    if not value:
        return f"{label} is required."
    if len(value) > 50:
        return f"{label} is too long (maximum 50 letters)."
    if not value.isalpha():
        return f"{label}: only letters are allowed (no digits, spaces or symbols)."
    return None


def validate_phone(value):
    """Digits only, 10-15 of them. Returns an error text or None."""
    if not value:
        return "Phone number is required."
    if not re.fullmatch(r"[0-9]+", value):
        return "Phone number: only digits are allowed (no letters or symbols)."
    if not 10 <= len(value) <= 15:
        return "Phone number must be between 10 and 15 digits long."
    return None


def validate_password(value):
    """At least 8 characters, with letters AND digits."""
    if len(value) < 8:
        return "Password must be at least 8 characters long."
    if len(value) > 128:
        return "Password is too long (maximum 128 characters)."
    if not any(c.isalpha() for c in value):
        return "Password must contain at least one letter."
    if not any(c.isdigit() for c in value):
        return "Password must contain at least one digit."
    return None


def validate_barber_password(value):
    """Barber passwords are set by the admin: at least 8 characters."""
    if len(value) < 8:
        return "Password must be at least 8 characters long."
    if len(value) > 128:
        return "Password is too long (maximum 128 characters)."
    return None


def normalize_name(value):
    """Ignore case, spaces and trailing dots: 'Arman', 'arman ' and 'ARMAN.' match."""
    return (value or "").strip(". ").casefold()


def barber_password_ok(stored, given):
    """
    Returns (is_correct, needs_rehash).
    Normal case: stored is a hash made by the admin panel.
    Convenience case: someone typed a plain-text password straight into the
    database; it is accepted once and then replaced by a proper hash.
    """
    if not stored:
        return False, False
    if stored.startswith(("scrypt:", "pbkdf2:")):
        return check_password_hash(stored, given), False
    return hmac.compare_digest(stored.encode(), given.encode()), True


# --------------------------------------------------
# SESSION HELPERS
# --------------------------------------------------

def current_customer():
    customer_id = session.get("customer_id")
    if not customer_id:
        return None

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM Customers WHERE customer_id = ?",
            (customer_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        session.pop("customer_id", None)
    return row


def current_barber():
    barber_id = session.get("barber_id")
    if not barber_id:
        return None

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM Barbers WHERE barber_id = ?",
            (barber_id,)
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        session.pop("barber_id", None)
    return row


def customer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_customer() is None:
            flash("Please log in or register to continue.", "error")
            return redirect(url_for("welcome"))
        return f(*args, **kwargs)
    return decorated_function


def render_auth(tab="login", form=None, errors=None, status=200):
    if tab not in ("login", "register", "barber"):
        tab = "login"
    return render_template(
        "auth.html", tab=tab, form=form or {}, errors=errors or {}
    ), status


# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

# True  = a client can rate a barber only after a COMPLETED visit with him.
# False = any logged-in client can rate any barber.
REQUIRE_COMPLETED_VISIT = True

# Appointments are kept for one year, then deleted automatically.
KEEP_APPOINTMENTS_DAYS = 365

# Walk-in clients without a phone get a placeholder phone like "walkin-ab12cd".
WALKIN_PREFIX = "walkin-"

# Letters, separated by single spaces / hyphens / apostrophes ("Anna-Maria")
WALKIN_NAME_RE = re.compile(r"^[^\W\d_]+(?:[ '\-][^\W\d_]+)*$")


# --------------------------------------------------
# SHARED QUERIES
# --------------------------------------------------

def revenue_start(time_filter, now=None):
    """First date of the revenue period for 'today' / 'weekly' / 'monthly'."""
    now = now or datetime.now()
    if time_filter == "weekly":
        return (now - timedelta(days=7)).strftime("%Y-%m-%d")
    if time_filter == "monthly":
        return (now - timedelta(days=30)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


def barbers_with_ratings(conn):
    """All barbers plus avg_rating (1 decimal) and rating_count."""
    return conn.execute("""
        SELECT
            Barbers.*,
            ROUND(AVG(BarberRatings.rating), 1) AS avg_rating,
            COUNT(BarberRatings.rating) AS rating_count
        FROM Barbers
        LEFT JOIN BarberRatings
            ON BarberRatings.barber_id = Barbers.barber_id
        GROUP BY Barbers.barber_id
        ORDER BY Barbers.barber_id
    """).fetchall()


def get_time_off(conn, today, barber_id=None):
    """
    Upcoming barber breaks, one entry per break (a multi-day break is stored
    as many hourly rows that share one break_group).
    """
    sql = """
        SELECT
            COALESCE(Appointments.break_group,
                     'row-' || Appointments.appointment_id) AS gkey,
            Barbers.full_name AS barber_name,
            MIN(Appointments.appointment_date || ' ' ||
                Appointments.appointment_time) AS first_slot,
            MAX(Appointments.appointment_date || ' ' ||
                Appointments.appointment_time) AS last_slot,
            COUNT(*) AS hours
        FROM Appointments
        JOIN Barbers ON Appointments.barber_id = Barbers.barber_id
        JOIN Services ON Appointments.service_id = Services.service_id
        WHERE Services.service_name = 'System Break'
          AND Appointments.appointment_date >= ?
    """
    params = [today]
    if barber_id is not None:
        sql += " AND Appointments.barber_id = ?"
        params.append(barber_id)
    sql += " GROUP BY gkey, Appointments.barber_id ORDER BY first_slot"

    result = []
    for row in conn.execute(sql, params).fetchall():
        last = datetime.strptime(row["last_slot"], "%Y-%m-%d %H:%M")
        result.append({
            "gkey": row["gkey"],
            "barber_name": row["barber_name"],
            "start": row["first_slot"],
            "end": (last + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            "hours": row["hours"],
        })
    return result


def get_break_ids(conn):
    """Ids of the hidden 'Admin - Break' customer and 'System Break' service."""
    customer = conn.execute(
        "SELECT customer_id FROM Customers WHERE phone = ?", (BREAK_PHONE,)
    ).fetchone()
    if customer:
        customer_id = customer["customer_id"]
    else:
        customer_id = conn.execute(
            "INSERT INTO Customers (full_name, phone) VALUES ('Admin - Break', ?)",
            (BREAK_PHONE,)
        ).lastrowid

    service = conn.execute(
        "SELECT service_id FROM Services WHERE service_name = 'System Break'"
    ).fetchone()
    if service:
        service_id = service["service_id"]
    else:
        service_id = conn.execute(
            "INSERT INTO Services (service_name, price, duration_minutes) "
            "VALUES ('System Break', 0, 60)"
        ).lastrowid

    return customer_id, service_id


# SQL snippet: show the phone only when it is a real one
PHONE_FOR_DISPLAY = (
    "CASE WHEN Customers.phone LIKE 'walkin-%' THEN '' "
    "ELSE Customers.phone END"
)


# --------------------------------------------------
# BOOKING TIME GRID, SEVERAL SERVICES, OVERLAP CHECKS
# --------------------------------------------------

BREAK_SERVICE = "System Break"
SLOT_MINUTES = 30                 # start times: 09:00, 09:30, ... 19:30
GRID_START = 9 * 60
GRID_END = 20 * 60                # exclusive: no booking may start at 20:00
BREAK_MINUTES = 60                # every break row blocks one hour
MAX_SERVICES_PER_BOOKING = 6


def to_minutes(hhmm):
    """'13:30' -> 810"""
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def fmt_hm(minutes):
    """810 -> '13:30'"""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


BOOKING_TIMES = [fmt_hm(m) for m in range(GRID_START, GRID_END, SLOT_MINUTES)]

# Length of a stored appointment: its own total, else its main service's length
APPT_MINUTES_SQL = "COALESCE(Appointments.duration_minutes, Services.duration_minutes)"

# Price of a stored appointment: all its services; old rows without
# AppointmentServices rows fall back to the main service.
# (Needs Appointments JOIN Services in the outer query.)
BOOKING_PRICE_SQL = """COALESCE(
    (SELECT SUM(s2.price)
       FROM AppointmentServices AS aps
       JOIN Services AS s2 ON s2.service_id = aps.service_id
      WHERE aps.appointment_id = Appointments.appointment_id),
    Services.price)"""


def offered_services(conn):
    """Services a client can book (never the internal 'System Break')."""
    return conn.execute(
        "SELECT * FROM Services WHERE service_name != ? ORDER BY service_id",
        (BREAK_SERVICE,)
    ).fetchall()


def busy_intervals(conn, barber_id, date, exclude_id=None):
    """
    Busy time of one barber on one date as sorted (start, end, kind) tuples in
    minutes since midnight. kind is 'break' or 'booked'. Cancelled
    appointments do not count.
    """
    rows = conn.execute(f"""
        SELECT Appointments.appointment_id,
               Appointments.appointment_time,
               {APPT_MINUTES_SQL} AS minutes,
               Services.service_name
        FROM Appointments
        JOIN Services ON Appointments.service_id = Services.service_id
        WHERE Appointments.barber_id = ?
          AND Appointments.appointment_date = ?
          AND Appointments.status != 'Cancelled'
    """, (barber_id, date)).fetchall()

    result = []
    for row in rows:
        if exclude_id is not None and row["appointment_id"] == exclude_id:
            continue
        start = to_minutes(row["appointment_time"])
        if row["service_name"] == BREAK_SERVICE:
            result.append((start, start + BREAK_MINUTES, "break"))
        else:
            result.append((start, start + (row["minutes"] or SLOT_MINUTES), "booked"))
    return sorted(result)


def fits(start, total, barber, busy, date=None, now=None):
    """
    Can a block of `total` minutes start at `start` (minutes since midnight)?
    Returns (ok, state, message). state is one of:
      free, past, outside, booked, break,
      too_long (would run past the end of the shift),
      overlap  (would run into a later booking or break).
    The past check is done only when `date` ('YYYY-MM-DD') is given.
    """
    work_start = to_minutes(barber["work_start"])
    work_end = to_minutes(barber["work_end"])
    end = start + total

    if date is not None:
        now = now or datetime.now()
        today = now.strftime("%Y-%m-%d")
        if date < today or (date == today and start <= now.hour * 60 + now.minute):
            return False, "past", "This time has already passed. Please choose a later time."

    if not (work_start <= start < work_end):
        return False, "outside", (
            f"The barber works {barber['work_start']} - {barber['work_end']}. "
            "Please choose a time in that range."
        )

    for b_start, b_end, kind in busy:
        if b_start <= start < b_end:
            if kind == "break":
                return False, "break", "The barber is off at this time. Please choose another time."
            return False, "booked", (
                "This time is already booked by another client. Please choose another time."
            )

    if end > work_end:
        return False, "too_long", (
            f"Not enough time: the selected services take {total} min "
            f"({fmt_hm(start)}–{fmt_hm(end)}), but the barber's shift ends at "
            f"{barber['work_end']}. Choose an earlier time or fewer services."
        )

    for b_start, b_end, kind in busy:
        if start < b_start < end:
            what = "the barber's time off" if kind == "break" else "the next booking"
            return False, "overlap", (
                f"Not enough time: the selected services take {total} min "
                f"({fmt_hm(start)}–{fmt_hm(end)}) and would overlap {what} "
                f"at {fmt_hm(b_start)}. Choose another time or fewer services."
            )

    return True, "free", ""


def selected_services(conn, form):
    """
    Services chosen in a booking form (checkboxes named service_ids; an old
    single service_id field is accepted too). Returns (services, error).
    The order of the form is kept: the first one becomes the main service.
    """
    raw = form.getlist("service_ids")
    if not raw and form.get("service_id"):
        raw = [form.get("service_id")]

    ids = []
    for value in raw:
        try:
            service_id = int(value)
        except (TypeError, ValueError):
            return None, "Please choose valid services."
        if service_id not in ids:
            ids.append(service_id)

    if not ids:
        return None, "Please choose at least one service."
    if len(ids) > MAX_SERVICES_PER_BOOKING:
        return None, f"Please choose at most {MAX_SERVICES_PER_BOOKING} services in one booking."

    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM Services WHERE service_id IN ({marks}) AND service_name != ?",
        (*ids, BREAK_SERVICE)
    ).fetchall()
    by_id = {row["service_id"]: row for row in rows}
    if len(by_id) != len(ids):
        return None, "One of the selected services does not exist."
    return [by_id[i] for i in ids], None


def insert_booking(conn, customer_id, barber_id, date, time_str, services):
    """
    Saves one booking with all its services. The caller has already checked
    that the time fits. A cancelled appointment still holds the same start
    time (UNIQUE rule), so it is removed first.
    """
    for (old_id,) in conn.execute("""
        SELECT appointment_id FROM Appointments
        WHERE barber_id = ? AND appointment_date = ?
          AND appointment_time = ? AND status = 'Cancelled'
    """, (barber_id, date, time_str)).fetchall():
        conn.execute("DELETE FROM AppointmentServices WHERE appointment_id = ?", (old_id,))
        conn.execute("DELETE FROM Appointments WHERE appointment_id = ?", (old_id,))

    total = sum(s["duration_minutes"] for s in services)
    appointment_id = conn.execute("""
        INSERT INTO Appointments
            (customer_id, barber_id, service_id, appointment_date,
             appointment_time, status, duration_minutes)
        VALUES (?, ?, ?, ?, ?, 'Booked', ?)
    """, (customer_id, barber_id, services[0]["service_id"],
          date, time_str, total)).lastrowid

    conn.executemany(
        "INSERT INTO AppointmentServices (appointment_id, service_id) VALUES (?, ?)",
        [(appointment_id, s["service_id"]) for s in services]
    )
    return appointment_id


# Columns every booking list query must select (used by with_services)
BOOKING_LIST_COLUMNS = """
    Appointments.appointment_id,
    Appointments.service_id,
    Appointments.appointment_date,
    Appointments.appointment_time,
    Appointments.status,
    Appointments.duration_minutes AS appt_minutes,
    Services.service_name AS service_name,
    Services.price AS price,
    Services.duration_minutes AS service_minutes
"""


def with_services(conn, rows):
    """
    Turns appointment rows into dicts with every service of the booking:
    services (list), service_names ('Fade Haircut + Beard Trim'),
    total_minutes, total_price and end_time.
    """
    ids = [row["appointment_id"] for row in rows]
    by_appt = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for s in conn.execute(f"""
            SELECT aps.appointment_id, Services.service_id, Services.service_name,
                   Services.price, Services.duration_minutes
            FROM AppointmentServices AS aps
            JOIN Services ON Services.service_id = aps.service_id
            WHERE aps.appointment_id IN ({marks})
        """, chunk):
            by_appt.setdefault(s["appointment_id"], []).append(dict(s))

    result = []
    for row in rows:
        item = dict(row)
        services = by_appt.get(row["appointment_id"]) or [{
            "service_id": row["service_id"],
            "service_name": row["service_name"],
            "price": row["price"],
            "duration_minutes": row["service_minutes"],
        }]
        # Main service first, the others in a fixed order
        services.sort(key=lambda s: (s["service_id"] != row["service_id"], s["service_id"]))

        total_minutes = row["appt_minutes"] or sum(s["duration_minutes"] for s in services)
        item["services"] = services
        item["service_names"] = " + ".join(s["service_name"] for s in services)
        item["total_minutes"] = total_minutes
        item["total_price"] = sum(s["price"] for s in services)
        item["end_time"] = fmt_hm(to_minutes(row["appointment_time"]) + total_minutes)
        result.append(item)
    return result


# --------------------------------------------------
# AUTOMATIC BACKUP
# --------------------------------------------------

def create_backup():
    if not DB_PATH.exists():
        print(f"BACKUP ERROR: database not found: {DB_PATH}")
        return None

    try:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime(
            "%Y-%m-%d_%H-%M-%S_%f"
        )
        backup_path = (
            BACKUP_DIR / f"barbershop_{timestamp}.sqlite"
        )

        with sqlite3.connect(DB_PATH, timeout=10) as source:
            with sqlite3.connect(backup_path) as destination:
                source.backup(destination)

        backups = sorted(
            BACKUP_DIR.glob("barbershop_*.sqlite"),
            key=lambda file: file.stat().st_mtime,
            reverse=True
        )

        for old_backup in backups[MAX_BACKUPS:]:
            old_backup.unlink(missing_ok=True)

        print(f"BACKUP OK: {backup_path.name}")
        return backup_path

    except (sqlite3.Error, OSError) as error:
        print(f"BACKUP ERROR: {error}")
        return None


# --------------------------------------------------
# DATABASE CLEANUP (AUTO-DELETE)
# --------------------------------------------------
_last_cleanup = 0.0
CLEANUP_EVERY_SECONDS = 60


@app.before_request
def auto_cleanup():
    global _last_cleanup

    # Skip cleanup for static files (CSS/images) to save processing power
    if request.endpoint == 'static':
        return

    # Run the cleanup at most once a minute instead of on every request
    if time.time() - _last_cleanup < CLEANUP_EVERY_SECONDS:
        return
    _last_cleanup = time.time()

    try:
        conn = get_db()
        
        # Appointments are kept for one year, then deleted
        cutoff = (datetime.now() - timedelta(days=KEEP_APPOINTMENTS_DAYS)).strftime("%Y-%m-%d %H:%M")

        conn.execute("""
            DELETE FROM Appointments
            WHERE appointment_date || ' ' || appointment_time < ?
        """, (cutoff,))

        # Service rows of deleted appointments (ON DELETE CASCADE does this
        # too; this also cleans up anything left by older code)
        conn.execute("""
            DELETE FROM AppointmentServices
            WHERE appointment_id NOT IN (SELECT appointment_id FROM Appointments)
        """)

        # Delete orphan customers (customers with no remaining appointments)
        # Note: We exclude the 'Admin - Break' dummy customer (phone 0000000000) so breaks still work!
        conn.execute("""
            DELETE FROM Customers 
            WHERE phone != '0000000000' 
            AND password_hash IS NULL  -- never delete registered accounts
            AND customer_id NOT IN (SELECT customer_id FROM Appointments)
        """)
        
        conn.commit()
        conn.close()
    except Exception as e:
        print("AUTO-CLEANUP ERROR:", e)

# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------

@app.route("/")
def index():
    # Barbers and the admin have their own pages.
    if session.get("barber_id"):
        return redirect(url_for("barber_dashboard"))
    if session.get("logged_in"):
        return redirect(url_for("admin"))

    # Customers must register or log in first.
    customer = current_customer()
    if customer is None:
        return redirect(url_for("welcome"))

    conn = get_db()
    # Get today's date in YYYY-MM-DD format
    today = datetime.now().strftime("%Y-%m-%d")

    try:
        barbers = barbers_with_ratings(conn)

        # "System Break" is an internal service: never offer it to clients
        services = offered_services(conn)

        # Barbers this client may rate (completed visit) + the client's ratings
        done_ids = {
            row[0] for row in conn.execute("""
                SELECT DISTINCT barber_id FROM Appointments
                WHERE customer_id = ? AND status = 'Completed'
            """, (customer["customer_id"],))
        }
        my_ratings = {
            row["barber_id"]: row["rating"] for row in conn.execute(
                "SELECT barber_id, rating FROM BarberRatings WHERE customer_id = ?",
                (customer["customer_id"],))
        }
        rateable = [
            b for b in barbers
            if (not REQUIRE_COMPLETED_VISIT) or b["barber_id"] in done_ids
        ]

        # Only THIS customer's appointments (old ones are removed by auto-cleanup)
        my_appointments = with_services(conn, conn.execute(f"""
            SELECT
                {BOOKING_LIST_COLUMNS},
                Barbers.full_name AS barber_name
            FROM Appointments
            JOIN Barbers
                ON Appointments.barber_id = Barbers.barber_id
            JOIN Services
                ON Appointments.service_id = Services.service_id
            WHERE Appointments.customer_id = ?
            ORDER BY
                Appointments.appointment_date ASC,
                Appointments.appointment_time ASC
        """, (customer["customer_id"],)).fetchall())

        # Upcoming = still booked and in the future (can be cancelled).
        # Everything else (completed, cancelled, time passed) goes to history.
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        upcoming = [
            a for a in my_appointments
            if a["status"] == "Booked"
            and f'{a["appointment_date"]} {a["appointment_time"]}' > now_str
        ]
        upcoming_ids = {a["appointment_id"] for a in upcoming}
        history = [a for a in my_appointments if a["appointment_id"] not in upcoming_ids]
        history.reverse()  # newest first

        return render_template(
            "index.html",
            barbers=barbers,
            services=services,
            booking_times=BOOKING_TIMES,
            min_duration=min((s["duration_minutes"] for s in services), default=SLOT_MINUTES),
            upcoming=upcoming,
            history=history,
            rateable=rateable,
            my_ratings=my_ratings,
            today=today,  # Passes today to index.html to block past dates in the form
            customer=customer
        )

    finally:
        conn.close()


# --------------------------------------------------
# BOOK AN APPOINTMENT
# --------------------------------------------------

@app.route("/book", methods=["POST"])
@customer_required
def book():
    # The customer comes from the login session, not from the form.
    customer_id = current_customer()["customer_id"]

    print("\n--- BOOKING REQUEST ---")
    print("Received fields:", list(request.form.keys()))

    def back(message, category="error"):
        flash(message, category)
        return redirect(url_for("index") + "#booking")

    barber_id = request.form.get("barber_id", "").strip()
    appointment_date = request.form.get("appointment_date", "").strip()
    appointment_time = request.form.get("appointment_time", "").strip()

    if not request.form.getlist("service_ids") and not request.form.get("service_id"):
        print("BOOKING REJECTED: no services")
        return back("Please choose at least one service.")

    if not barber_id or not appointment_date or not appointment_time:
        print("BOOKING REJECTED: missing fields")
        return back("Please fill in all fields.")

    try:
        barber_id = int(barber_id)
        datetime.strptime(appointment_date, "%Y-%m-%d")
        datetime.strptime(appointment_time, "%H:%M")
    except ValueError as error:
        print("BOOKING REJECTED: invalid input:", error)
        return back("Please enter a valid date, time, barber and service.")

    if appointment_time not in BOOKING_TIMES:
        print("BOOKING REJECTED: not on the 30-minute grid:", appointment_time)
        return back("Please choose a start time from the list (every 30 minutes).")

    conn = None

    try:
        conn = get_db()

        barber = conn.execute("""
            SELECT barber_id, work_start, work_end
            FROM Barbers
            WHERE barber_id = ?
        """, (barber_id,)).fetchone()

        if barber is None:
            print("BOOKING REJECTED: barber not found:", barber_id)
            return back("The selected barber does not exist.")

        services, error = selected_services(conn, request.form)
        if error:
            print("BOOKING REJECTED:", error)
            return back(error)
        total = sum(s["duration_minutes"] for s in services)

        # Lock the database for writing, so two clients cannot both pass the
        # overlap check for overlapping times at the same moment.
        conn.execute("BEGIN IMMEDIATE")

        ok, state, message = fits(
            to_minutes(appointment_time), total, barber,
            busy_intervals(conn, barber_id, appointment_date),
            date=appointment_date
        )
        if not ok:
            conn.rollback()
            print("BOOKING REJECTED:", state)
            return back(message)

        insert_booking(conn, customer_id, barber_id,
                       appointment_date, appointment_time, services)
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM Appointments"
        ).fetchone()[0]

        print("BOOKING SAVED SUCCESSFULLY")
        print("Database:", DB_PATH)
        print("Appointments count:", count)

        end_time = fmt_hm(to_minutes(appointment_time) + total)
        names = " + ".join(s["service_name"] for s in services)
        done = (f"Appointment booked successfully! {appointment_date}, "
                f"{appointment_time}–{end_time}: {names} ({total} min).")

        # A backup failure must not undo a saved booking.
        backup_path = create_backup()

        if backup_path:
            flash(done, "success")
        else:
            flash(done + " The backup could not be created.", "success")

    except sqlite3.IntegrityError as error:
        if conn is not None:
            conn.rollback()

        print("BOOKING DATABASE CONSTRAINT ERROR:", error)
        flash(
            "This time may already be booked, "
            "or the submitted data violates a database rule.", "error"
        )

    except (sqlite3.Error, OSError) as error:
        if conn is not None:
            conn.rollback()

        print("BOOKING DATABASE ERROR:", repr(error))
        flash("A database error occurred. Check the VS Code terminal.", "error")

    finally:
        if conn is not None:
            conn.close()

    return redirect(url_for("index"))

# --------------------------------------------------
# CUSTOMER: RATE A BARBER (1-5 stars)
# --------------------------------------------------

@app.route("/rate/<int:barber_id>", methods=["POST"])
@customer_required
def rate_barber(barber_id):
    customer_id = current_customer()["customer_id"]

    def back(message, category="error"):
        flash(message, category)
        return redirect(url_for("index") + "#rate-barbers")

    try:
        rating = int(request.form.get("rating", ""))
    except ValueError:
        rating = 0
    if not 1 <= rating <= 5:
        return back("Please choose from 1 to 5 stars.")

    conn = get_db()
    try:
        barber = conn.execute(
            "SELECT barber_id, full_name FROM Barbers WHERE barber_id = ?",
            (barber_id,)
        ).fetchone()
        if barber is None:
            return back("This barber does not exist.")

        if REQUIRE_COMPLETED_VISIT:
            visited = conn.execute("""
                SELECT COUNT(*) FROM Appointments
                WHERE customer_id = ? AND barber_id = ? AND status = 'Completed'
            """, (customer_id, barber_id)).fetchone()[0]
            if not visited:
                return back("You can rate a barber after a completed visit.")

        # One rating per client per barber; rating again replaces the old one
        conn.execute("""
            INSERT INTO BarberRatings (customer_id, barber_id, rating, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(customer_id, barber_id)
            DO UPDATE SET rating = excluded.rating,
                          updated_at = excluded.updated_at
        """, (customer_id, barber_id, rating,
              datetime.now().strftime("%Y-%m-%d %H:%M")))
        conn.commit()
        create_backup()
        flash(f"Thank you! You rated {barber['full_name']}: {rating}/5.", "success")
    except sqlite3.Error as error:
        conn.rollback()
        print("RATE ERROR:", error)
        flash("Database error occurred. Please try again.", "error")
    finally:
        conn.close()

    return redirect(url_for("index") + "#rate-barbers")


# --------------------------------------------------
# CUSTOMER: CANCEL OWN APPOINTMENT
# --------------------------------------------------

@app.route("/cancel/<int:appointment_id>", methods=["POST"])
@customer_required
def cancel_appointment(appointment_id):
    customer_id = current_customer()["customer_id"]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    conn = get_db()
    try:
        # Only the owner can cancel, only if still booked and not in the past.
        cursor = conn.execute("""
            UPDATE Appointments
            SET status = 'Cancelled'
            WHERE appointment_id = ?
              AND customer_id = ?
              AND status = 'Booked'
              AND appointment_date || ' ' || appointment_time > ?
        """, (appointment_id, customer_id, now_str))
        conn.commit()

        if cursor.rowcount:
            create_backup()
            flash("Your appointment has been cancelled.", "success")
        else:
            flash("This appointment cannot be cancelled.", "error")
    except sqlite3.Error as error:
        conn.rollback()
        print("CANCEL ERROR:", error)
        flash("Database error occurred. Please try again.", "error")
    finally:
        conn.close()

    return redirect(url_for("index") + "#my-appointments")


# --------------------------------------------------
# AUTHENTICATION (CUSTOMER / BARBER / ADMIN) & ADMIN DASHBOARD
# --------------------------------------------------

# Security decorator to protect the admin page
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            flash("Please log in to access the dashboard.", "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Security decorator to protect the barber page
def barber_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_barber() is None:
            flash("Please log in as a barber to access this page.", "error")
            return redirect(url_for("welcome", tab="barber"))
        return f(*args, **kwargs)
    return decorated_function


# ---------- Welcome page: Log in / Register / Barber ----------

@app.route("/welcome")
def welcome():
    if current_customer() is not None:
        return redirect(url_for("index"))
    return render_auth(request.args.get("tab", "login"))


# ---------- Customer registration ----------

@app.route("/register", methods=["POST"])
def register():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")

    form = {"first_name": first_name, "last_name": last_name, "phone": phone}

    errors = {}
    for key, error in (
        ("first_name", validate_name(first_name, "First name")),
        ("last_name", validate_name(last_name, "Last name")),
        ("phone", validate_phone(phone)),
        ("password", validate_password(password)),
    ):
        if error:
            errors[key] = error

    if not errors and phone == BREAK_PHONE:
        errors["phone"] = "This phone number cannot be used."

    if errors:
        return render_auth("register", form, errors, 400)

    first_name = first_name.capitalize()
    last_name = last_name.capitalize()
    full_name = f"{first_name} {last_name}"
    password_hash = generate_password_hash(password)

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT customer_id, password_hash FROM Customers WHERE phone = ?",
            (phone,)
        ).fetchone()

        if existing is not None and existing["password_hash"]:
            flash(
                "An account with this phone number already exists. "
                "Please log in.", "error"
            )
            return render_auth("register", form, {}, 409)

        if existing is not None:
            # Old record without a password (created before accounts
            # existed): attach the account to it, keeping its history.
            customer_id = existing["customer_id"]
            conn.execute("""
                UPDATE Customers
                SET full_name = ?, first_name = ?, last_name = ?,
                    password_hash = ?
                WHERE customer_id = ?
            """, (full_name, first_name, last_name,
                  password_hash, customer_id))
        else:
            cursor = conn.execute("""
                INSERT INTO Customers
                    (full_name, first_name, last_name, phone, password_hash)
                VALUES (?, ?, ?, ?, ?)
            """, (full_name, first_name, last_name, phone, password_hash))
            customer_id = cursor.lastrowid

        conn.commit()

    except sqlite3.IntegrityError:
        conn.rollback()
        flash(
            "An account with this phone number already exists. "
            "Please log in.", "error"
        )
        return render_auth("register", form, {}, 409)

    except sqlite3.Error as error:
        conn.rollback()
        print("REGISTER ERROR:", error)
        flash("Database error occurred. Please try again.", "error")
        return render_auth("register", form, {}, 500)

    finally:
        conn.close()

    create_backup()

    session.clear()
    session["customer_id"] = customer_id
    flash(f"Account created. Welcome, {first_name}!", "success")
    return redirect(url_for("index"))


# ---------- Customer login (phone + password) ----------

@app.route("/customer_login", methods=["POST"])
def customer_login():
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    form = {"phone": phone}

    if not phone or not password:
        flash("Please enter your phone number and password.", "error")
        return render_auth("login", form, {}, 400)

    phone_error = validate_phone(phone)
    if phone_error and not re.fullmatch(r"[0-9]+", phone):
        return render_auth("login", form, {"phone": phone_error}, 400)

    conn = get_db()
    try:
        customer = conn.execute(
            "SELECT customer_id, password_hash FROM Customers WHERE phone = ?",
            (phone,)
        ).fetchone()
    finally:
        conn.close()

    if (
        customer is None
        or not customer["password_hash"]
        or not check_password_hash(customer["password_hash"], password)
    ):
        flash("Invalid phone number or password.", "error")
        return render_auth("login", form, {}, 401)

    session.clear()
    session["customer_id"] = customer["customer_id"]
    return redirect(url_for("index"))


# ---------- Barber login (first name + last name + password) ----------

@app.route("/barber_login", methods=["POST"])
def barber_login():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    password = request.form.get("password", "")
    form = {"first_name": first_name, "last_name": last_name}

    if not first_name or not last_name or not password:
        flash("Please enter your first name, last name and password.", "error")
        return render_auth("barber", form, {}, 400)

    conn = get_db()
    try:
        barbers = conn.execute("SELECT * FROM Barbers").fetchall()
    finally:
        conn.close()

    matches = [
        b for b in barbers
        if normalize_name(b["first_name"]) == normalize_name(first_name)
        and normalize_name(b["last_name"]) == normalize_name(last_name)
    ]

    barber = None
    rehash = False
    for b in matches:
        ok, needs_rehash = barber_password_ok(b["password_hash"], password)
        if ok:
            barber, rehash = b, needs_rehash
            break

    if barber is None:
        flash(
            "Invalid name or password. Barber passwords are assigned "
            "by the administrator.", "error"
        )
        return render_auth("barber", form, {}, 401)

    if rehash:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE Barbers SET password_hash = ? WHERE barber_id = ?",
                (generate_password_hash(password), barber["barber_id"])
            )
            conn.commit()
        finally:
            conn.close()

    session.clear()
    session["barber_id"] = barber["barber_id"]
    return redirect(url_for("barber_dashboard"))


# ---------- Admin login (unchanged credentials) ----------

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        # For a prototype, hardcoded credentials are fine.
        # In a real app, check a hashed password from the database.
        if request.form.get("username") == "admin" and request.form.get("password") == "admin123":
            session.clear()
            session['logged_in'] = True
            flash("Welcome to the Admin Dashboard.", "success")
            return redirect(url_for("admin"))
        else:
            flash("Invalid credentials. Please try again.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("index"))


# ---------- Free / busy start times for the booking forms ----------

@app.route("/availability")
def availability():
    """
    JSON for the booking forms (client and admin): for one barber, one date
    and one total length (?duration=minutes, the sum of the chosen services),
    the state of every 30-minute start time:
      free, booked, break, outside, past,
      too_long (would run past the end of the shift),
      overlap  (would run into a later booking or break).
    """
    if current_customer() is None and not session.get("logged_in"):
        return jsonify({"error": "login required"}), 401

    barber_id = request.args.get("barber_id", type=int)
    date = request.args.get("date", "")
    duration = request.args.get("duration", type=int)

    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return jsonify({"error": "bad date"}), 400
    if barber_id is None:
        return jsonify({"error": "bad barber"}), 400

    conn = get_db()
    try:
        barber = conn.execute(
            "SELECT work_start, work_end FROM Barbers WHERE barber_id = ?",
            (barber_id,)
        ).fetchone()
        if barber is None:
            return jsonify({"error": "no such barber"}), 404

        shortest = min(
            (s["duration_minutes"] for s in offered_services(conn)),
            default=SLOT_MINUTES
        )
        if duration is None:
            duration = shortest
        duration = max(1, min(duration, 24 * 60))

        busy = busy_intervals(conn, barber_id, date)
    finally:
        conn.close()

    now = datetime.now()
    slots = {}
    free_shortest = 0
    for t in BOOKING_TIMES:
        _, state, _ = fits(to_minutes(t), duration, barber, busy, date=date, now=now)
        slots[t] = state
        # Would at least the shortest service fit here? (lets the form say
        # "your combination is too long" instead of "the day is full")
        if fits(to_minutes(t), shortest, barber, busy, date=date, now=now)[0]:
            free_shortest += 1

    return jsonify({
        "slots": slots,
        "duration": duration,
        "work_start": barber["work_start"],
        "work_end": barber["work_end"],
        "free_count": sum(1 for s in slots.values() if s == "free"),
        "free_count_shortest": free_shortest,
    })


# ---------- Change password (customer and barber) ----------

def password_change_error(current_ok, current, new, confirm, validator):
    """Returns an error text, or None when the change is allowed."""
    if not current or not new or not confirm:
        return "Please fill in all three password fields."
    if not current_ok:
        return "The current password is wrong."
    if new != confirm:
        return "The new password and its confirmation do not match."
    if new == current:
        return "The new password must be different from the current one."
    return validator(new)


@app.route("/account/password", methods=["POST"])
@customer_required
def change_password():
    customer = current_customer()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    current_ok = bool(customer["password_hash"]) and check_password_hash(
        customer["password_hash"], current
    )
    error = password_change_error(current_ok, current, new, confirm, validate_password)
    if error:
        flash(error, "error")
        return redirect(url_for("index") + "#password")

    conn = get_db()
    try:
        conn.execute(
            "UPDATE Customers SET password_hash = ? WHERE customer_id = ?",
            (generate_password_hash(new), customer["customer_id"])
        )
        conn.commit()
    finally:
        conn.close()

    flash("Password changed successfully.", "success")
    return redirect(url_for("index") + "#password")


@app.route("/barber/password", methods=["POST"])
@barber_required
def barber_change_password():
    barber = current_barber()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    current_ok, _ = barber_password_ok(barber["password_hash"], current)
    error = password_change_error(
        current_ok, current, new, confirm, validate_barber_password
    )
    if error:
        flash(error, "error")
        return redirect(url_for("barber_dashboard") + "#password")

    conn = get_db()
    try:
        conn.execute(
            "UPDATE Barbers SET password_hash = ? WHERE barber_id = ?",
            (generate_password_hash(new), barber["barber_id"])
        )
        conn.commit()
    finally:
        conn.close()

    flash("Password changed successfully.", "success")
    return redirect(url_for("barber_dashboard") + "#password")


# ---------- Barber dashboard ----------

@app.route("/barber")
@barber_required
def barber_dashboard():
    barber = current_barber()
    today_dt = datetime.now()
    today = today_dt.strftime("%Y-%m-%d")
    two_weeks_ago = (today_dt - timedelta(days=14)).strftime("%Y-%m-%d")

    time_filter = request.args.get("filter", "today")
    start_date = revenue_start(time_filter, today_dt)

    conn = get_db()
    try:
        # Revenue from this barber's COMPLETED services in the chosen period
        revenue_row = conn.execute(f"""
            SELECT SUM({BOOKING_PRICE_SQL}) AS total_revenue, COUNT(*) AS done_count
            FROM Appointments
            JOIN Services ON Appointments.service_id = Services.service_id
            WHERE Appointments.barber_id = ?
              AND Appointments.status = 'Completed'
              AND Appointments.appointment_date >= ?
              AND Appointments.appointment_date <= ?
        """, (barber["barber_id"], start_date, today)).fetchone()
        total_revenue = revenue_row["total_revenue"] or 0
        done_count = revenue_row["done_count"] or 0

        rating = conn.execute("""
            SELECT ROUND(AVG(rating), 1) AS avg_rating, COUNT(*) AS rating_count
            FROM BarberRatings WHERE barber_id = ?
        """, (barber["barber_id"],)).fetchone()

        # Upcoming appointments (+ still-"Booked" ones from the last 14 days).
        # Breaks are shown separately in "Time off".
        appointments = with_services(conn, conn.execute(f"""
            SELECT
                {BOOKING_LIST_COLUMNS},
                Customers.full_name AS customer_name,
                {PHONE_FOR_DISPLAY} AS customer_phone
            FROM Appointments
            JOIN Customers ON Appointments.customer_id = Customers.customer_id
            JOIN Services ON Appointments.service_id = Services.service_id
            WHERE Appointments.barber_id = ?
              AND Services.service_name != 'System Break'
              AND (Appointments.appointment_date >= ?
                   OR (Appointments.status = 'Booked'
                       AND Appointments.appointment_date >= ?))
            ORDER BY
                Appointments.appointment_date ASC,
                Appointments.appointment_time ASC
            LIMIT 50
        """, (barber["barber_id"], today, two_weeks_ago)).fetchall())

        time_off = get_time_off(conn, today, barber["barber_id"])
    finally:
        conn.close()

    return render_template(
        "barber.html",
        barber=barber,
        appointments=appointments,
        today=today,
        total_revenue=total_revenue,
        done_count=done_count,
        time_filter=time_filter,
        rating=rating,
        time_off=time_off
    )


@app.route("/barber/update_status/<int:appointment_id>", methods=["POST"])
@barber_required
def barber_update_status(appointment_id):
    new_status = request.form.get("status")

    if new_status not in ("Completed", "Cancelled"):
        flash("Invalid status update.", "error")
        return redirect(url_for("barber_dashboard"))

    barber_id = session["barber_id"]

    conn = get_db()
    try:
        # A barber can only change their OWN real appointments (not breaks).
        cursor = conn.execute("""
            UPDATE Appointments
            SET status = ?
            WHERE appointment_id = ?
              AND barber_id = ?
              AND service_id IN (
                  SELECT service_id FROM Services
                  WHERE service_name != 'System Break'
              )
        """, (new_status, appointment_id, barber_id))
        conn.commit()

        if cursor.rowcount:
            create_backup()
            flash(f"Appointment #{appointment_id} marked as {new_status}.", "success")
        else:
            flash("Appointment not found.", "error")
    except sqlite3.Error as error:
        conn.rollback()
        flash("Database error occurred while updating status.", "error")
        print("BARBER UPDATE ERROR:", error)
    finally:
        conn.close()

    return redirect(url_for("barber_dashboard"))


@app.route("/admin")
@login_required
def admin():
    conn = get_db()

    today_dt = datetime.now()
    today_str = today_dt.strftime("%Y-%m-%d")
    two_weeks_ago = (today_dt - timedelta(days=14)).strftime("%Y-%m-%d")

    # Get the time filter from the URL (defaults to 'today')
    time_filter = request.args.get("filter", "today")
    start_date = revenue_start(time_filter, today_dt)

    try:
        # Revenue for Completed appointments in the selected date range
        revenue_row = conn.execute(f"""
            SELECT SUM({BOOKING_PRICE_SQL}) as total_revenue
            FROM Appointments
            JOIN Services ON Appointments.service_id = Services.service_id
            WHERE Appointments.status = 'Completed'
              AND Appointments.appointment_date >= ?
              AND Appointments.appointment_date <= ?
        """, (start_date, today_str)).fetchone()

        total_revenue = revenue_row['total_revenue'] if revenue_row['total_revenue'] else 0

        barbers = barbers_with_ratings(conn)

        services = offered_services(conn)

        # Clients for the "Add an appointment" form (not the hidden break client)
        customers = conn.execute(f"""
            SELECT
                customer_id,
                full_name,
                {PHONE_FOR_DISPLAY.replace('Customers.', '')} AS phone
            FROM Customers
            WHERE phone != ?
            ORDER BY full_name COLLATE NOCASE
        """, (BREAK_PHONE,)).fetchall()

        # Appointments from today on, plus still-"Booked" ones from the last
        # 14 days (so they can still be marked Completed / Cancelled).
        # Barber breaks are shown separately in "Time off".
        appointments = with_services(conn, conn.execute(f"""
            SELECT
                {BOOKING_LIST_COLUMNS},
                Customers.full_name AS customer_name,
                {PHONE_FOR_DISPLAY} AS customer_phone,
                Barbers.full_name AS barber_name
            FROM Appointments
            JOIN Customers ON Appointments.customer_id = Customers.customer_id
            JOIN Barbers ON Appointments.barber_id = Barbers.barber_id
            JOIN Services ON Appointments.service_id = Services.service_id
            WHERE Services.service_name != 'System Break'
              AND (Appointments.appointment_date >= ?
                   OR (Appointments.status = 'Booked'
                       AND Appointments.appointment_date >= ?))
            ORDER BY
                Appointments.appointment_date ASC,
                Appointments.appointment_time ASC
            LIMIT 50
        """, (today_str, two_weeks_ago)).fetchall())

        time_off = get_time_off(conn, today_str)

        return render_template(
            "admin.html",
            appointments=appointments,
            barbers=barbers,
            services=services,
            booking_times=BOOKING_TIMES,
            min_duration=min((s["duration_minutes"] for s in services), default=SLOT_MINUTES),
            customers=customers,
            time_off=time_off,
            today=today_str,
            total_revenue=total_revenue,
            time_filter=time_filter
        )
    finally:
        conn.close()


# --------------------------------------------------
# ADMIN: ADD AN APPOINTMENT FOR A CLIENT (registered or walk-in)
# --------------------------------------------------

@app.route("/admin/add_appointment", methods=["POST"])
@login_required
def add_appointment():
    mode = request.form.get("client_mode", "existing")
    barber_id = request.form.get("barber_id", "").strip()
    appointment_date = request.form.get("appointment_date", "").strip()
    appointment_time = request.form.get("appointment_time", "").strip()

    def back(message, category="error"):
        flash(message, category)
        return redirect(url_for("admin") + "#admin-appt-form")

    has_services = request.form.getlist("service_ids") or request.form.get("service_id")
    if not all([barber_id, has_services, appointment_date, appointment_time]):
        return back("Please choose a barber, at least one service, a date and a time.")

    try:
        barber_id = int(barber_id)
        date_obj = datetime.strptime(appointment_date, "%Y-%m-%d").date()
        datetime.strptime(appointment_time, "%H:%M")
    except ValueError:
        return back("Please enter a valid date and time.")

    if date_obj < datetime.now().date():
        return back("You cannot add an appointment for a day that has passed.")
    if appointment_time not in BOOKING_TIMES:
        return back("Please choose a start time from the list (every 30 minutes).")

    # ---- Check the client fields before touching the database ----
    walkin_name = ""
    walkin_phone = ""
    customer_id = None

    if mode == "walkin":
        walkin_name = " ".join(request.form.get("walkin_name", "").split())
        walkin_phone = request.form.get("walkin_phone", "").strip()

        if not walkin_name:
            return back("Please enter the client's name.")
        if len(walkin_name) > 100 or not WALKIN_NAME_RE.match(walkin_name):
            return back("Client name: only letters are allowed (no digits or symbols).")
        if walkin_phone:
            phone_error = validate_phone(walkin_phone)
            if phone_error:
                return back(phone_error)
            if walkin_phone == BREAK_PHONE:
                return back("This phone number cannot be used.")
    else:
        try:
            customer_id = int(request.form.get("customer_id", ""))
        except ValueError:
            return back("Please choose a client from the list.")

    conn = get_db()
    try:
        barber = conn.execute(
            "SELECT barber_id, full_name, work_start, work_end "
            "FROM Barbers WHERE barber_id = ?", (barber_id,)
        ).fetchone()
        if barber is None:
            return back("The selected barber does not exist.")

        services, error = selected_services(conn, request.form)
        if error:
            return back(error)
        total = sum(s["duration_minutes"] for s in services)

        # Lock for writing, then check the whole block [start, start + total)
        conn.execute("BEGIN IMMEDIATE")
        ok, _, message = fits(
            to_minutes(appointment_time), total, barber,
            busy_intervals(conn, barber_id, appointment_date),
            date=appointment_date
        )
        if not ok:
            conn.rollback()
            return back(f"{barber['full_name']}: {message}")

        # ---- Find or create the client ----
        if mode == "walkin":
            existing = None
            if walkin_phone:
                existing = conn.execute(
                    "SELECT customer_id, full_name FROM Customers WHERE phone = ?",
                    (walkin_phone,)
                ).fetchone()

            if existing is not None:
                # Phone already known (maybe a registered account): reuse it.
                customer_id = existing["customer_id"]
                client_label = existing["full_name"]
            else:
                parts = walkin_name.split(" ", 1)
                phone_value = walkin_phone or f"{WALKIN_PREFIX}{uuid.uuid4().hex[:12]}"
                customer_id = conn.execute("""
                    INSERT INTO Customers (full_name, first_name, last_name, phone)
                    VALUES (?, ?, ?, ?)
                """, (walkin_name, parts[0], parts[1] if len(parts) > 1 else "",
                      phone_value)).lastrowid
                client_label = walkin_name
        else:
            customer = conn.execute(
                "SELECT customer_id, full_name FROM Customers "
                "WHERE customer_id = ? AND phone != ?",
                (customer_id, BREAK_PHONE)
            ).fetchone()
            if customer is None:
                return back("The selected client does not exist.")
            client_label = customer["full_name"]

        insert_booking(conn, customer_id, barber_id,
                       appointment_date, appointment_time, services)
        conn.commit()
        create_backup()
        end_time = fmt_hm(to_minutes(appointment_time) + total)
        names = " + ".join(s["service_name"] for s in services)
        flash(
            f"Appointment added for {client_label}: {appointment_date}, "
            f"{appointment_time}–{end_time} with {barber['full_name']} "
            f"({names}, {total} min).", "success"
        )

    except sqlite3.IntegrityError:
        conn.rollback()
        flash("That time is already taken for this barber (booking or break).", "error")
    except sqlite3.Error as error:
        conn.rollback()
        print("ADMIN ADD APPOINTMENT ERROR:", error)
        flash("Database error occurred while adding the appointment.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin"))


# --------------------------------------------------
# ADMIN: BARBER TIME OFF (hours, several days, any length up to a year)
# --------------------------------------------------

@app.route("/add_break", methods=["POST"])
@login_required
def add_break():
    barber_id = request.form.get("barber_id", "").strip()
    start_date = request.form.get("start_date", "").strip()
    end_date = request.form.get("end_date", "").strip()
    start_time = request.form.get("start_time", "").strip()
    end_time = request.form.get("end_time", "").strip()
    full_days = request.form.get("full_days") == "on"

    def back(message, category="error"):
        flash(message, category)
        return redirect(url_for("admin"))

    if not barber_id or not start_date or not end_date:
        return back("Please choose a barber and the start and end dates.")
    if not full_days and (not start_time or not end_time):
        return back("Please choose the start and end time, or tick 'Full days'.")

    try:
        barber_id = int(barber_id)
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date()

        if full_days:
            start_dt = datetime.combine(start_d, dtime(0, 0))
            end_dt = datetime.combine(end_d + timedelta(days=1), dtime(0, 0))
        else:
            start_dt = datetime.combine(
                start_d, datetime.strptime(start_time, "%H:%M").time())
            end_dt = datetime.combine(
                end_d, datetime.strptime(end_time, "%H:%M").time())
    except ValueError:
        return back("Please enter valid dates and times.")

    if start_d < datetime.now().date():
        return back("Time off cannot start on a day that has already passed.")
    if end_dt <= start_dt:
        return back("The end of the time off must be after its start.")
    if (end_dt - start_dt) > timedelta(days=366):
        return back("Time off can be at most one year long.")

    conn = get_db()
    try:
        barber = conn.execute(
            "SELECT barber_id, full_name, work_start, work_end "
            "FROM Barbers WHERE barber_id = ?", (barber_id,)
        ).fetchone()
        if barber is None:
            return back("The selected barber does not exist.")

        first_hour = int(barber["work_start"][:2])
        last_hour = int(barber["work_end"][:2])  # exclusive

        # Every working hour of this barber that falls inside the period
        slots = []
        day = start_dt.date()
        while datetime.combine(day, dtime(0, 0)) < end_dt:
            for hour in range(first_hour, last_hour):
                slot = datetime.combine(day, dtime(hour, 0))
                if start_dt <= slot < end_dt:
                    slots.append(slot)
            day += timedelta(days=1)

        if not slots:
            return back(
                f"No working hours of {barber['full_name']} "
                f"({barber['work_start']} - {barber['work_end']}) "
                "fall inside this period."
            )

        break_customer_id, break_service_id = get_break_ids(conn)
        group = uuid.uuid4().hex
        blocked = 0
        skipped = []

        # Client bookings per day (they can be longer than an hour and
        # start at :30), so an hour that overlaps one is not blocked.
        bookings_by_day = {}

        for slot in slots:
            d = slot.strftime("%Y-%m-%d")
            t = slot.strftime("%H:%M")

            if d not in bookings_by_day:
                bookings_by_day[d] = [
                    (b_start, b_end) for b_start, b_end, kind
                    in busy_intervals(conn, barber_id, d) if kind == "booked"
                ]
            start = to_minutes(t)
            if any(b_start < start + BREAK_MINUTES and start < b_end
                   for b_start, b_end in bookings_by_day[d]):
                skipped.append(f"{d} {t}")
                continue

            existing = conn.execute("""
                SELECT appointment_id, status FROM Appointments
                WHERE barber_id = ? AND appointment_date = ? AND appointment_time = ?
            """, (barber_id, d, t)).fetchone()

            if existing is not None:
                if existing["status"] == "Cancelled":
                    conn.execute("DELETE FROM AppointmentServices WHERE appointment_id = ?",
                                 (existing["appointment_id"],))
                    conn.execute("DELETE FROM Appointments WHERE appointment_id = ?",
                                 (existing["appointment_id"],))
                else:
                    skipped.append(f"{d} {t}")
                    continue

            conn.execute("""
                INSERT INTO Appointments
                    (customer_id, barber_id, service_id, appointment_date,
                     appointment_time, status, break_group)
                VALUES (?, ?, ?, ?, ?, 'Booked', ?)
            """, (break_customer_id, barber_id, break_service_id, d, t, group))
            blocked += 1

        conn.commit()

        if blocked:
            create_backup()

        period = (f"{start_dt:%Y-%m-%d %H:%M} to {end_dt:%Y-%m-%d %H:%M}"
                  if not full_days else f"{start_d} to {end_d} (full days)")
        message = (f"Time off added for {barber['full_name']}: "
                   f"{blocked} hour(s) blocked, {period}.")
        if skipped:
            shown = ", ".join(skipped[:5]) + (" ..." if len(skipped) > 5 else "")
            message += (f" {len(skipped)} hour(s) were NOT blocked because they "
                        f"already have bookings ({shown}). Cancel those "
                        "bookings first, then add the time off again.")
        flash(message, "success" if blocked else "error")

    except sqlite3.Error as error:
        conn.rollback()
        print("BREAK ERROR:", error)
        flash("Database error occurred while adding time off.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin"))


@app.route("/delete_time_off/<group_key>", methods=["POST"])
@login_required
def delete_time_off(group_key):
    conn = get_db()
    try:
        if group_key.startswith("row-") and group_key[4:].isdigit():
            # Old single-row break created before time-off groups existed
            conn.execute("DELETE FROM Appointments WHERE appointment_id = ?",
                         (int(group_key[4:]),))
        else:
            conn.execute("DELETE FROM Appointments WHERE break_group = ?",
                         (group_key,))
        conn.commit()
        create_backup()
        flash("Time off removed. The hours are open for booking again.", "success")
    except sqlite3.Error as error:
        conn.rollback()
        print("DELETE TIME OFF ERROR:", error)
        flash("Database error occurred while removing time off.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin"))


@app.route("/add_barber", methods=["POST"])
@login_required
def add_barber():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    password = request.form.get("password", "")
    work_start = request.form.get("work_start")
    work_end = request.form.get("work_end")

    if not all([work_start, work_end]):
        flash("All fields are required to add a barber.", "error")
        return redirect(url_for("admin"))

    for error in (
        validate_name(first_name, "Barber first name"),
        validate_name(last_name, "Barber last name"),
        validate_barber_password(password),
    ):
        if error:
            flash(error, "error")
            return redirect(url_for("admin"))

    if work_start >= work_end:
        flash("Work end time must be after start time.", "error")
        return redirect(url_for("admin"))

    first_name = first_name.capitalize()
    last_name = last_name.capitalize()
    full_name = f"{first_name} {last_name}"

    conn = get_db()
    try:
        # Barbers log in by name, so two barbers must not share one.
        for b in conn.execute(
            "SELECT first_name, last_name FROM Barbers"
        ).fetchall():
            if (
                normalize_name(b["first_name"]) == normalize_name(first_name)
                and normalize_name(b["last_name"]) == normalize_name(last_name)
            ):
                flash("A barber with this name already exists.", "error")
                return redirect(url_for("admin"))

        conn.execute("""
            INSERT INTO Barbers
                (full_name, first_name, last_name, password_hash,
                 work_start, work_end)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (full_name, first_name, last_name,
              generate_password_hash(password), work_start, work_end))
        conn.commit()
        create_backup()
        flash(f"Barber {full_name} added successfully!", "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash("Database error occurred while adding barber.", "error")
        print("ADD BARBER ERROR:", error)
    finally:
        conn.close()

    return redirect(url_for("admin"))


@app.route("/set_barber_password/<int:barber_id>", methods=["POST"])
@login_required
def set_barber_password(barber_id):
    password = request.form.get("password", "")

    error = validate_barber_password(password)
    if error:
        flash(error, "error")
        return redirect(url_for("admin"))

    conn = get_db()
    try:
        cursor = conn.execute(
            "UPDATE Barbers SET password_hash = ? WHERE barber_id = ?",
            (generate_password_hash(password), barber_id)
        )
        conn.commit()
        if cursor.rowcount:
            create_backup()
            flash("Barber password saved.", "success")
        else:
            flash("Barber not found.", "error")
    except sqlite3.Error as error:
        conn.rollback()
        flash("Database error occurred while saving the password.", "error")
        print("SET BARBER PASSWORD ERROR:", error)
    finally:
        conn.close()

    return redirect(url_for("admin"))


@app.route("/delete_break/<int:appointment_id>", methods=["POST"])
@login_required
def delete_break(appointment_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM Appointments WHERE appointment_id = ?", (appointment_id,))
        conn.commit()
        create_backup()
        flash("Break removed successfully! The time slot is now open for customers.", "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash("Database error occurred while removing break.", "error")
        print("DELETE BREAK ERROR:", error)
    finally:
        conn.close()

    return redirect(url_for("admin"))

@app.route("/delete_barber/<int:barber_id>", methods=["POST"])
@login_required
def delete_barber(barber_id):
    conn = get_db()
    try:
        # Prevent deletion if the barber has appointments
        appt_count = conn.execute(
            "SELECT COUNT(*) FROM Appointments WHERE barber_id = ?", 
            (barber_id,)
        ).fetchone()[0]
        
        if appt_count > 0:
            flash("Cannot delete this barber because they have booked appointments in the system.", "error")
        else:
            conn.execute("DELETE FROM Barbers WHERE barber_id = ?", (barber_id,))
            conn.commit()
            create_backup()
            flash("Barber removed successfully.", "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash("Database error occurred while deleting barber.", "error")
        print("DELETE BARBER ERROR:", error)
    finally:
        conn.close()

    return redirect(url_for("admin"))


@app.route("/update_status/<int:appointment_id>", methods=["POST"])
@login_required
def update_status(appointment_id):
    new_status = request.form.get("status")
    
    if new_status not in ['Booked', 'Completed', 'Cancelled']:
        flash("Invalid status update.", "error")
        return redirect(url_for("admin"))

    conn = get_db()
    try:
        # Making a cancelled booking active again: its time must still be free
        if new_status == "Booked":
            appt = conn.execute(f"""
                SELECT Appointments.barber_id, Appointments.appointment_date,
                       Appointments.appointment_time, Appointments.status,
                       {APPT_MINUTES_SQL} AS minutes
                FROM Appointments
                JOIN Services ON Appointments.service_id = Services.service_id
                WHERE Appointments.appointment_id = ?
            """, (appointment_id,)).fetchone()
            if appt is not None and appt["status"] == "Cancelled":
                barber = conn.execute(
                    "SELECT work_start, work_end FROM Barbers WHERE barber_id = ?",
                    (appt["barber_id"],)
                ).fetchone()
                ok, _, message = fits(
                    to_minutes(appt["appointment_time"]), appt["minutes"], barber,
                    busy_intervals(conn, appt["barber_id"], appt["appointment_date"],
                                   exclude_id=appointment_id)
                )
                if not ok:
                    flash(f"Appointment #{appointment_id} cannot be restored. {message}", "error")
                    return redirect(url_for("admin"))

        conn.execute("""
            UPDATE Appointments
            SET status = ?
            WHERE appointment_id = ?
        """, (new_status, appointment_id))
        conn.commit()
        
        # Create a backup after a successful modification
        create_backup()
        
        flash(f"Appointment #{appointment_id} marked as {new_status}.", "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash("Database error occurred while updating status.", "error")
        print("UPDATE ERROR:", error)
    finally:
        conn.close()

    return redirect(url_for("admin"))

# --------------------------------------------------
# START APPLICATION
# --------------------------------------------------

if __name__ == "__main__":
    print("Using database:", DB_PATH)

    # Avoid making two startup backups with Flask's reloader.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        create_backup()

    app.run(debug=True)