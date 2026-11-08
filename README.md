# BarberBook: online appointment service for a barbershop

ICT course final project, group SE-2619 (AITU). Team: Almasuly Dias, Almabek Yernar.

Customers register, choose a barber, a service, a date and a time, and book without
messaging anyone. Barbers see their own schedule. The administrator manages barbers,
bookings, time off and sees revenue.

**Live version:** https://barberbook-com.onrender.com/welcome
(free hosting: the first request after a pause can take about a minute to wake the server)

## Technology (as built)

| Layer | Used |
|---|---|
| Server | Python 3, Flask (server-rendered HTML templates) |
| Database | SQLite file `barbershop.sqlite` (schema in `schema.sql`) |
| Client | HTML, CSS (`static/style.css`), a little JavaScript, no framework |
| Security | scrypt password hashes (Werkzeug), sessions, server-side validation, `UNIQUE (barber_id, date, time)` against double booking |
| Backups | automatic copy of the database to `backups/` after each change (last 7 kept) |
| Media | SVG logo, WebM video (VP9), WebP photo, MP3 sounds |

## Run locally (Windows)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python init_db.py                 # creates barbershop.sqlite with demo barbers, services and bookings
python app.py
```

Open http://127.0.0.1:5000/welcome

`init_db.py` only fills an empty database, so it is safe to run again.

## Test accounts

| Role | How to log in | Login | Password |
|---|---|---|---|
| Customer | "Log in" tab (phone + password) | `87777777777` | `Kazakstan2008` |
| Barber | "I'm a barber" tab (first name, last name, password) | `Arman` / `S` | `12345678` |
| Administrator | `/login` | `admin` | `admin123` |

The administrator login is fixed in `app.py` (prototype). Customer and barber accounts live
in the database, so on the hosted version create them again: register a customer on the
site, and add a barber with a password in the admin panel (Manage Barbers).

## What each role can do

- **Customer:** register and log in, see services and barbers, pick a free time (past hours are blocked), book, see and cancel own appointments, rate a barber after a completed visit.
- **Barber:** see own schedule, mark appointments Completed or Cancelled.
- **Administrator:** add appointments for registered or walk-in clients, add or remove barbers and set their passwords, block time off, change appointment status, see revenue (today / 7 days / 30 days).

## Files

```
app.py                    all routes and logic
schema.sql, init_db.py    database structure and demo data
templates/                index (customer), auth, login, barber, admin
static/                   style.css, logo (svg), video (webm), photo, audio
backups/                  automatic database copies (created at run time)
```

## Known limitations

- SQLite on a free host can lose data when the server is rebuilt; a real deployment needs a managed database and a server inside Kazakhstan for personal data.
- No CSRF tokens, no login attempt limit, the secret key and the admin password are written in `app.py`: acceptable for a prototype, not for production.
- No online payment, mobile app, e-mail or SMS reminders (out of scope in Stage 1).
