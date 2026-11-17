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

from urllib.parse import urlsplit

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, session, jsonify
)
from werkzeug.exceptions import HTTPException

from translations import (
    LANGUAGES, LANG_COOKIE, LANG_COOKIE_MAX_AGE,
    get_lang, t, tn, money, minutes, service_label
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


# --------------------------------------------------
# LANGUAGE (English / Russian / Kazakh), see translations.py
# --------------------------------------------------

# Every template (also macros imported from _booking.html) can use
# _("text") / t("text", name=...), tn("{n} rating", count), money(4000), minutes(45).
app.jinja_env.globals.update(_=t, t=t, tn=tn, money=money, minutes=minutes,
                             service_label=service_label, LANGUAGES=LANGUAGES)


@app.context_processor
def inject_language():
    """Current language code for <html lang> and the EN / RU / KZ switch."""
    return {"lang": get_lang()}


@app.route("/lang/<code>")
def set_language(code):
    """Remembers the chosen language (session + cookie for a year) and goes back."""
    target = url_for("index")
    # The page to return to: ?next=/path (sent by the switch itself, works even
    # when the phone's browser sends no Referer), otherwise the Referer.
    next_path = request.args.get("next", "")
    if next_path.startswith("/") and not next_path.startswith("//") and "\\" not in next_path:
        referrer = request.host_url.rstrip("/") + next_path
    else:
        referrer = request.referrer
    if referrer:
        parts = urlsplit(referrer)
        if parts.scheme in ("http", "https") and parts.netloc == request.host:
            # Pages shown after a POST (e.g. a failed login) cannot be opened
            # with GET: go to the home page instead of a 405 error.
            try:
                app.url_map.bind(request.host).match(parts.path, method="GET")
                target = parts.path + ("?" + parts.query if parts.query else "")
            except HTTPException:
                pass

    response = redirect(target)
    if code in LANGUAGES:
        session["lang"] = code
        response.set_cookie(LANG_COOKIE, code, max_age=LANG_COOKIE_MAX_AGE,
                            samesite="Lax", httponly=True)
    return response

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

        # Services: names in Russian / Kazakh set by the admin, and a
        # "removed" flag (is_active = 0) that keeps old bookings intact
        service_cols = columns("Services")
        for col in ("name_ru", "name_kk"):
            if col not in service_cols:
                conn.execute(f"ALTER TABLE Services ADD COLUMN {col} TEXT")
        if "is_active" not in service_cols:
            conn.execute("ALTER TABLE Services ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")

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
        return t("{label} is required.", label=label)
    if len(value) > 50:
        return t("{label} is too long (maximum 50 letters).", label=label)
    if not value.isalpha():
        return t("{label}: only letters are allowed (no digits, spaces or symbols).", label=label)
    return None


def validate_phone(value):
    """Digits only, 10-15 of them. Returns an error text or None."""
    if not value:
        return t("Phone number is required.")
    if not re.fullmatch(r"[0-9]+", value):
        return t("Phone number: only digits are allowed (no letters or symbols).")
    if not 10 <= len(value) <= 15:
        return t("Phone number must be between 10 and 15 digits long.")
    return None


def validate_password(value):
    """At least 8 characters, with letters AND digits."""
    if len(value) < 8:
        return t("Password must be at least 8 characters long.")
    if len(value) > 128:
        return t("Password is too long (maximum 128 characters).")
    if not any(c.isalpha() for c in value):
        return t("Password must contain at least one letter.")
    if not any(c.isdigit() for c in value):
        return t("Password must contain at least one digit.")
    return None


def validate_barber_password(value):
    """Barber passwords are set by the admin: at least 8 characters."""
    if len(value) < 8:
        return t("Password must be at least 8 characters long.")
    if len(value) > 128:
        return t("Password is too long (maximum 128 characters).")
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
            flash(t("Please log in or register to continue."), "error")
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
    """Services a client can book (active ones, never the internal 'System Break')."""
    return conn.execute(
        "SELECT * FROM Services WHERE service_name != ? AND is_active = 1 "
        "ORDER BY service_id",
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
            return False, "past", t("This time has already passed. Please choose a later time.")

    if not (work_start <= start < work_end):
        return False, "outside", t(
            "The barber works {start}–{end}. Please choose a time in that range.",
            start=barber["work_start"], end=barber["work_end"]
        )

    for b_start, b_end, kind in busy:
        if b_start <= start < b_end:
            if kind == "break":
                return False, "break", t("The barber is off at this time. Please choose another time.")
            return False, "booked", t(
                "This time is already booked by another client. Please choose another time."
            )

    if end > work_end:
        return False, "too_long", t(
            "Not enough time: the selected services take {total} min ({start}–{end}), "
            "but the barber's shift ends at {work_end}. "
            "Choose an earlier time or fewer services.",
            total=total, start=fmt_hm(start), end=fmt_hm(end), work_end=barber["work_end"]
        )

    for b_start, b_end, kind in busy:
        if start < b_start < end:
            if kind == "break":
                text = ("Not enough time: the selected services take {total} min "
                        "({start}–{end}) and would overlap the barber's time off "
                        "at {at}. Choose another time or fewer services.")
            else:
                text = ("Not enough time: the selected services take {total} min "
                        "({start}–{end}) and would overlap the next booking "
                        "at {at}. Choose another time or fewer services.")
            return False, "overlap", t(
                text, total=total, start=fmt_hm(start), end=fmt_hm(end), at=fmt_hm(b_start)
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
            return None, t("Please choose valid services.")
        if service_id not in ids:
            ids.append(service_id)

    if not ids:
        return None, t("Please choose at least one service.")
    if len(ids) > MAX_SERVICES_PER_BOOKING:
        return None, t("Please choose at most {max} services in one booking.", max=MAX_SERVICES_PER_BOOKING)

    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM Services WHERE service_id IN ({marks}) AND service_name != ?",
        (*ids, BREAK_SERVICE)
    ).fetchall()
    by_id = {row["service_id"]: row for row in rows}
    if len(by_id) != len(ids):
        return None, t("One of the selected services does not exist.")
    if any(not row["is_active"] for row in rows):
        return None, t("One of the selected services is no longer offered. Please choose another one.")
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
    Services.name_ru AS service_name_ru,
    Services.name_kk AS service_name_kk,
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
                   Services.name_ru, Services.name_kk,
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
            "name_ru": row["service_name_ru"],
            "name_kk": row["service_name_kk"],
            "price": row["price"],
            "duration_minutes": row["service_minutes"],
        }]
        # Main service first, the others in a fixed order
        services.sort(key=lambda s: (s["service_id"] != row["service_id"], s["service_id"]))

        total_minutes = row["appt_minutes"] or sum(s["duration_minutes"] for s in services)
        item["services"] = services
        item["service_names"] = " + ".join(service_label(s) for s in services)
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
        return back(t("Please choose at least one service."))

    if not barber_id or not appointment_date or not appointment_time:
        print("BOOKING REJECTED: missing fields")
        return back(t("Please fill in all fields."))

    try:
        barber_id = int(barber_id)
        datetime.strptime(appointment_date, "%Y-%m-%d")
        datetime.strptime(appointment_time, "%H:%M")
    except ValueError as error:
        print("BOOKING REJECTED: invalid input:", error)
        return back(t("Please enter a valid date, time, barber and service."))

    if appointment_time not in BOOKING_TIMES:
        print("BOOKING REJECTED: not on the 30-minute grid:", appointment_time)
        return back(t("Please choose a start time from the list (every 30 minutes)."))

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
            return back(t("The selected barber does not exist."))

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
        names = " + ".join(service_label(s) for s in services)
        done = t(
            "Appointment booked successfully! {date}, {start}–{end}: {services} ({minutes} min).",
            date=appointment_date, start=appointment_time, end=end_time,
            services=names, minutes=total
        )

        # A backup failure must not undo a saved booking.
        backup_path = create_backup()

        if backup_path:
            flash(done, "success")
        else:
            flash(done + " " + t("The backup could not be created."), "success")

    except sqlite3.IntegrityError as error:
        if conn is not None:
            conn.rollback()

        print("BOOKING DATABASE CONSTRAINT ERROR:", error)
        flash(
            t("This time may already be booked, "
              "or the submitted data violates a database rule."), "error"
        )

    except (sqlite3.Error, OSError) as error:
        if conn is not None:
            conn.rollback()

        print("BOOKING DATABASE ERROR:", repr(error))
        flash(t("A database error occurred. Check the VS Code terminal."), "error")

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
        return back(t("Please choose from 1 to 5 stars."))

    conn = get_db()
    try:
        barber = conn.execute(
            "SELECT barber_id, full_name FROM Barbers WHERE barber_id = ?",
            (barber_id,)
        ).fetchone()
        if barber is None:
            return back(t("This barber does not exist."))

        if REQUIRE_COMPLETED_VISIT:
            visited = conn.execute("""
                SELECT COUNT(*) FROM Appointments
                WHERE customer_id = ? AND barber_id = ? AND status = 'Completed'
            """, (customer_id, barber_id)).fetchone()[0]
            if not visited:
                return back(t("You can rate a barber after a completed visit."))

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
        flash(t("Thank you! You rated {name}: {rating}/5.",
                name=barber["full_name"], rating=rating), "success")
    except sqlite3.Error as error:
        conn.rollback()
        print("RATE ERROR:", error)
        flash(t("Database error occurred. Please try again."), "error")
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
            flash(t("Your appointment has been cancelled."), "success")
        else:
            flash(t("This appointment cannot be cancelled."), "error")
    except sqlite3.Error as error:
        conn.rollback()
        print("CANCEL ERROR:", error)
        flash(t("Database error occurred. Please try again."), "error")
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
            flash(t("Please log in to access the dashboard."), "error")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Security decorator to protect the barber page
def barber_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_barber() is None:
            flash(t("Please log in as a barber to access this page."), "error")
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
        ("first_name", validate_name(first_name, t("First name"))),
        ("last_name", validate_name(last_name, t("Last name"))),
        ("phone", validate_phone(phone)),
        ("password", validate_password(password)),
    ):
        if error:
            errors[key] = error

    if not errors and phone == BREAK_PHONE:
        errors["phone"] = t("This phone number cannot be used.")

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
                t("An account with this phone number already exists. Please log in."), "error"
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
            t("An account with this phone number already exists. Please log in."), "error"
        )
        return render_auth("register", form, {}, 409)

    except sqlite3.Error as error:
        conn.rollback()
        print("REGISTER ERROR:", error)
        flash(t("Database error occurred. Please try again."), "error")
        return render_auth("register", form, {}, 500)

    finally:
        conn.close()

    create_backup()

    session.clear()
    session["customer_id"] = customer_id
    flash(t("Account created. Welcome, {name}!", name=first_name), "success")
    return redirect(url_for("index"))


# ---------- Customer login (phone + password) ----------

@app.route("/customer_login", methods=["POST"])
def customer_login():
    phone = request.form.get("phone", "").strip()
    password = request.form.get("password", "")
    form = {"phone": phone}

    if not phone or not password:
        flash(t("Please enter your phone number and password."), "error")
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
        flash(t("Invalid phone number or password."), "error")
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
        flash(t("Please enter your first name, last name and password."), "error")
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
            t("Invalid name or password. Barber passwords are assigned by the administrator."),
            "error"
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
            flash(t("Welcome to the Admin Dashboard."), "success")
            return redirect(url_for("admin"))
        else:
            flash(t("Invalid credentials. Please try again."), "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash(t("You have been logged out."), "success")
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
    for hhmm in BOOKING_TIMES:
        _, state, _ = fits(to_minutes(hhmm), duration, barber, busy, date=date, now=now)
        slots[hhmm] = state
        # Would at least the shortest service fit here? (lets the form say
        # "your combination is too long" instead of "the day is full")
        if fits(to_minutes(hhmm), shortest, barber, busy, date=date, now=now)[0]:
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
        return t("Please fill in all three password fields.")
    if not current_ok:
        return t("The current password is wrong.")
    if new != confirm:
        return t("The new password and its confirmation do not match.")
    if new == current:
        return t("The new password must be different from the current one.")
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

    flash(t("Password changed successfully."), "success")
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

    flash(t("Password changed successfully."), "success")
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
        flash(t("Invalid status update."), "error")
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
            flash(t("Appointment #{id} marked as {status}.",
                  id=appointment_id, status=t(new_status)), "success")
        else:
            flash(t("Appointment not found."), "error")
    except sqlite3.Error as error:
        conn.rollback()
        flash(t("Database error occurred while updating status."), "error")
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
        managed_services = services_for_admin(conn, today_str)

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
            managed_services=managed_services,
            service_limits=SERVICE_LIMITS,
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
        return back(t("Please choose a barber, at least one service, a date and a time."))

    try:
        barber_id = int(barber_id)
        date_obj = datetime.strptime(appointment_date, "%Y-%m-%d").date()
        datetime.strptime(appointment_time, "%H:%M")
    except ValueError:
        return back(t("Please enter a valid date and time."))

    if date_obj < datetime.now().date():
        return back(t("You cannot add an appointment for a day that has passed."))
    if appointment_time not in BOOKING_TIMES:
        return back(t("Please choose a start time from the list (every 30 minutes)."))

    # ---- Check the client fields before touching the database ----
    walkin_name = ""
    walkin_phone = ""
    customer_id = None

    if mode == "walkin":
        walkin_name = " ".join(request.form.get("walkin_name", "").split())
        walkin_phone = request.form.get("walkin_phone", "").strip()

        if not walkin_name:
            return back(t("Please enter the client's name."))
        if len(walkin_name) > 100 or not WALKIN_NAME_RE.match(walkin_name):
            return back(t("Client name: only letters are allowed (no digits or symbols)."))
        if walkin_phone:
            phone_error = validate_phone(walkin_phone)
            if phone_error:
                return back(phone_error)
            if walkin_phone == BREAK_PHONE:
                return back(t("This phone number cannot be used."))
    else:
        try:
            customer_id = int(request.form.get("customer_id", ""))
        except ValueError:
            return back(t("Please choose a client from the list."))

    conn = get_db()
    try:
        barber = conn.execute(
            "SELECT barber_id, full_name, work_start, work_end "
            "FROM Barbers WHERE barber_id = ?", (barber_id,)
        ).fetchone()
        if barber is None:
            return back(t("The selected barber does not exist."))

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
                return back(t("The selected client does not exist."))
            client_label = customer["full_name"]

        insert_booking(conn, customer_id, barber_id,
                       appointment_date, appointment_time, services)
        conn.commit()
        create_backup()
        end_time = fmt_hm(to_minutes(appointment_time) + total)
        names = " + ".join(service_label(s) for s in services)
        flash(t(
            "Appointment added for {client}: {date}, {start}–{end} with {barber} "
            "({services}, {minutes} min).",
            client=client_label, date=appointment_date, start=appointment_time,
            end=end_time, barber=barber["full_name"], services=names, minutes=total
        ), "success")

    except sqlite3.IntegrityError:
        conn.rollback()
        flash(t("That time is already taken for this barber (booking or break)."), "error")
    except sqlite3.Error as error:
        conn.rollback()
        print("ADMIN ADD APPOINTMENT ERROR:", error)
        flash(t("Database error occurred while adding the appointment."), "error")
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
        return back(t("Please choose a barber and the start and end dates."))
    if not full_days and (not start_time or not end_time):
        return back(t("Please choose the start and end time, or tick 'Full days'."))

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
        return back(t("Please enter valid dates and times."))

    if start_d < datetime.now().date():
        return back(t("Time off cannot start on a day that has already passed."))
    if end_dt <= start_dt:
        return back(t("The end of the time off must be after its start."))
    if (end_dt - start_dt) > timedelta(days=366):
        return back(t("Time off can be at most one year long."))

    conn = get_db()
    try:
        barber = conn.execute(
            "SELECT barber_id, full_name, work_start, work_end "
            "FROM Barbers WHERE barber_id = ?", (barber_id,)
        ).fetchone()
        if barber is None:
            return back(t("The selected barber does not exist."))

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
            return back(t(
                "No working hours of {name} ({start}–{end}) fall inside this period.",
                name=barber["full_name"], start=barber["work_start"], end=barber["work_end"]
            ))

        break_customer_id, break_service_id = get_break_ids(conn)
        group = uuid.uuid4().hex
        blocked = 0
        skipped = []

        # Client bookings per day (they can be longer than an hour and
        # start at :30), so an hour that overlaps one is not blocked.
        bookings_by_day = {}

        for slot in slots:
            d = slot.strftime("%Y-%m-%d")
            hhmm = slot.strftime("%H:%M")

            if d not in bookings_by_day:
                bookings_by_day[d] = [
                    (b_start, b_end) for b_start, b_end, kind
                    in busy_intervals(conn, barber_id, d) if kind == "booked"
                ]
            start = to_minutes(hhmm)
            if any(b_start < start + BREAK_MINUTES and start < b_end
                   for b_start, b_end in bookings_by_day[d]):
                skipped.append(f"{d} {hhmm}")
                continue

            existing = conn.execute("""
                SELECT appointment_id, status FROM Appointments
                WHERE barber_id = ? AND appointment_date = ? AND appointment_time = ?
            """, (barber_id, d, hhmm)).fetchone()

            if existing is not None:
                if existing["status"] == "Cancelled":
                    conn.execute("DELETE FROM AppointmentServices WHERE appointment_id = ?",
                                 (existing["appointment_id"],))
                    conn.execute("DELETE FROM Appointments WHERE appointment_id = ?",
                                 (existing["appointment_id"],))
                else:
                    skipped.append(f"{d} {hhmm}")
                    continue

            conn.execute("""
                INSERT INTO Appointments
                    (customer_id, barber_id, service_id, appointment_date,
                     appointment_time, status, break_group)
                VALUES (?, ?, ?, ?, ?, 'Booked', ?)
            """, (break_customer_id, barber_id, break_service_id, d, hhmm, group))
            blocked += 1

        conn.commit()

        if blocked:
            create_backup()

        if full_days:
            period = t("{start} to {end} (full days)", start=start_d, end=end_d)
        else:
            period = t("{start} to {end}",
                       start=f"{start_dt:%Y-%m-%d %H:%M}", end=f"{end_dt:%Y-%m-%d %H:%M}")
        message = t("Time off added for {name}: {blocked}, {period}.",
                    name=barber["full_name"],
                    blocked=tn("{n} working hour blocked", blocked), period=period)
        if skipped:
            shown = ", ".join(skipped[:5]) + (" ..." if len(skipped) > 5 else "")
            message += " " + tn("{n} hour was NOT blocked", len(skipped), list=shown)
        flash(message, "success" if blocked else "error")

    except sqlite3.Error as error:
        conn.rollback()
        print("BREAK ERROR:", error)
        flash(t("Database error occurred while adding time off."), "error")
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
        flash(t("Time off removed. The hours are open for booking again."), "success")
    except sqlite3.Error as error:
        conn.rollback()
        print("DELETE TIME OFF ERROR:", error)
        flash(t("Database error occurred while removing time off."), "error")
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
        flash(t("All fields are required to add a barber."), "error")
        return redirect(url_for("admin"))

    for error in (
        validate_name(first_name, t("Barber first name")),
        validate_name(last_name, t("Barber last name")),
        validate_barber_password(password),
    ):
        if error:
            flash(error, "error")
            return redirect(url_for("admin"))

    if work_start >= work_end:
        flash(t("Work end time must be after start time."), "error")
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
                flash(t("A barber with this name already exists."), "error")
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
        flash(t("Barber {name} added successfully!", name=full_name), "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash(t("Database error occurred while adding barber."), "error")
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
            flash(t("Barber password saved."), "success")
        else:
            flash(t("Barber not found."), "error")
    except sqlite3.Error as error:
        conn.rollback()
        flash(t("Database error occurred while saving the password."), "error")
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
        flash(t("Break removed successfully! The time slot is now open for customers."), "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash(t("Database error occurred while removing break."), "error")
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
            flash(t("Cannot delete this barber because they have booked appointments in the system."), "error")
        else:
            conn.execute("DELETE FROM Barbers WHERE barber_id = ?", (barber_id,))
            conn.commit()
            create_backup()
            flash(t("Barber removed successfully."), "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash(t("Database error occurred while deleting barber."), "error")
        print("DELETE BARBER ERROR:", error)
    finally:
        conn.close()

    return redirect(url_for("admin"))


# --------------------------------------------------
# ADMIN: MANAGE SERVICES (add / remove / restore)
# A service that bookings refer to is only deactivated (is_active = 0):
# it can no longer be booked, but old bookings keep its name and price,
# so revenue stays correct. A never-used service is deleted for good.
# --------------------------------------------------

SERVICE_LIMITS = {
    "name_min": 2, "name_max": 60,
    "minutes_min": 5, "minutes_max": 480,
    "price_max": 1000000,
}

# Bookings (any status, any date) that use a service, as main or extra service
SERVICE_USE_SQL = """
    SELECT COUNT(*) FROM Appointments AS a
    WHERE (a.service_id = Services.service_id
           OR EXISTS (SELECT 1 FROM AppointmentServices AS aps
                      WHERE aps.appointment_id = a.appointment_id
                        AND aps.service_id = Services.service_id))
"""


def services_for_admin(conn, today_str):
    """All services except 'System Break', active ones first, with
    upcoming = number of upcoming 'Booked' appointments using them."""
    return conn.execute(f"""
        SELECT Services.*,
               ({SERVICE_USE_SQL}
                  AND a.status = 'Booked' AND a.appointment_date >= ?) AS upcoming
        FROM Services
        WHERE service_name != ?
        ORDER BY is_active DESC, service_id
    """, (today_str, BREAK_SERVICE)).fetchall()


def services_back(message, category="error"):
    flash(message, category)
    return redirect(url_for("admin") + "#services")


@app.route("/admin/services/add", methods=["POST"])
@login_required
def add_service():
    name = " ".join(request.form.get("service_name", "").split())
    name_ru = " ".join(request.form.get("name_ru", "").split())
    name_kk = " ".join(request.form.get("name_kk", "").split())
    price_raw = request.form.get("price", "").strip().replace(",", ".")
    minutes_raw = request.form.get("duration_minutes", "").strip()
    lim = SERVICE_LIMITS

    if not name:
        return services_back(t("Please enter the service name in English."))
    for value in (name, name_ru, name_kk):
        if value and not lim["name_min"] <= len(value) <= lim["name_max"]:
            return services_back(t("Service names must be {min}–{max} characters long.",
                                   min=lim["name_min"], max=lim["name_max"]))

    try:
        price = float(price_raw)
        if price != price or price in (float("inf"), float("-inf")):
            raise ValueError
    except ValueError:
        return services_back(t("Please enter the price as a number."))
    if not 0 <= price <= lim["price_max"]:
        return services_back(t("The price must be between 0 and {max} KZT.", max=lim["price_max"]))

    try:
        duration = int(minutes_raw)
    except ValueError:
        return services_back(t("Please enter the duration as a whole number of minutes."))
    if not lim["minutes_min"] <= duration <= lim["minutes_max"]:
        return services_back(t("The duration must be between {min} and {max} minutes.",
                               min=lim["minutes_min"], max=lim["minutes_max"]))

    conn = get_db()
    try:
        # Names are unique (also against removed services and 'System Break')
        same = conn.execute(
            "SELECT is_active FROM Services WHERE lower(service_name) = lower(?)", (name,)
        ).fetchone()
        if same is not None:
            if not same["is_active"] and name.lower() != BREAK_SERVICE.lower():
                return services_back(t("A removed service with this name already exists. Restore it instead."))
            return services_back(t("A service with this name already exists."))

        conn.execute("""
            INSERT INTO Services (service_name, name_ru, name_kk, price,
                                  duration_minutes, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (name, name_ru or None, name_kk or None, price, duration))
        conn.commit()
        create_backup()
        label = service_label({"service_name": name, "name_ru": name_ru, "name_kk": name_kk})
        return services_back(t("Service {name} added.", name=label), "success")
    except sqlite3.Error as error:
        conn.rollback()
        print("ADD SERVICE ERROR:", error)
        return services_back(t("Database error occurred while saving the service."))
    finally:
        conn.close()


def find_service(conn, service_id):
    """The service row, or None for unknown ids and the internal 'System Break'."""
    row = conn.execute("SELECT * FROM Services WHERE service_id = ?", (service_id,)).fetchone()
    if row is None or row["service_name"] == BREAK_SERVICE:
        return None
    return row


@app.route("/admin/services/<int:service_id>/delete", methods=["POST"])
@login_required
def delete_service(service_id):
    conn = get_db()
    try:
        service = find_service(conn, service_id)
        if service is None:
            return services_back(t("Service not found."))
        label = service_label(service)

        used = conn.execute(f"""
            SELECT ({SERVICE_USE_SQL}) FROM Services WHERE service_id = ?
        """, (service_id,)).fetchone()[0]
        if used:
            conn.execute("UPDATE Services SET is_active = 0 WHERE service_id = ?", (service_id,))
            message = t("Service {name} removed from booking. Existing bookings keep it.", name=label)
        else:
            conn.execute("DELETE FROM Services WHERE service_id = ?", (service_id,))
            message = t("Service {name} deleted.", name=label)
        conn.commit()
        create_backup()
        return services_back(message, "success")
    except sqlite3.Error as error:
        conn.rollback()
        print("DELETE SERVICE ERROR:", error)
        return services_back(t("Database error occurred while saving the service."))
    finally:
        conn.close()


@app.route("/admin/services/<int:service_id>/restore", methods=["POST"])
@login_required
def restore_service(service_id):
    conn = get_db()
    try:
        service = find_service(conn, service_id)
        if service is None:
            return services_back(t("Service not found."))
        conn.execute("UPDATE Services SET is_active = 1 WHERE service_id = ?", (service_id,))
        conn.commit()
        create_backup()
        return services_back(t("Service {name} can be booked again.", name=service_label(service)),
                             "success")
    except sqlite3.Error as error:
        conn.rollback()
        print("RESTORE SERVICE ERROR:", error)
        return services_back(t("Database error occurred while saving the service."))
    finally:
        conn.close()


@app.route("/update_status/<int:appointment_id>", methods=["POST"])
@login_required
def update_status(appointment_id):
    new_status = request.form.get("status")
    
    if new_status not in ['Booked', 'Completed', 'Cancelled']:
        flash(t("Invalid status update."), "error")
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
                    flash(t("Appointment #{id} cannot be restored. {reason}",
                            id=appointment_id, reason=message), "error")
                    return redirect(url_for("admin"))

        conn.execute("""
            UPDATE Appointments
            SET status = ?
            WHERE appointment_id = ?
        """, (new_status, appointment_id))
        conn.commit()
        
        # Create a backup after a successful modification
        create_backup()
        
        flash(t("Appointment #{id} marked as {status}.",
                  id=appointment_id, status=t(new_status)), "success")
    except sqlite3.Error as error:
        conn.rollback()
        flash(t("Database error occurred while updating status."), "error")
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