
import sqlite3
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "barbershop.sqlite"
SCHEMA_PATH = BASE_DIR / "schema.sql"

# Create the database and tables
with sqlite3.connect(DB_PATH) as connection:
    connection.execute("PRAGMA foreign_keys = ON")

    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    connection.executescript(schema)

    # Add barbers
    if connection.execute("SELECT COUNT(*) FROM Barbers").fetchone()[0] == 0:
        barbers = [
            ("Arman S.", "Arman", "S", "09:00", "18:00"),
            ("Nurlan K.", "Nurlan", "K", "10:00", "19:00"),
            ("Daniyar T.", "Daniyar", "T", "09:00", "17:00"),
            ("Alikhan M.", "Alikhan", "M", "11:00", "20:00"),
            ("Timur B.", "Timur", "B", "10:00", "18:00"),
        ]
        # Barbers have no password yet: the admin sets it in the admin panel.
        connection.executemany(
            """INSERT INTO Barbers
               (full_name, first_name, last_name, work_start, work_end)
               VALUES (?, ?, ?, ?, ?)""",
            barbers
        )

    # Add services
    if connection.execute("SELECT COUNT(*) FROM Services").fetchone()[0] == 0:
        services = [
            ("Classic Haircut", 4000, 30),
            ("Fade Haircut", 5000, 45),
            ("Beard Trim", 2500, 20),
            ("Haircut and Beard", 6000, 60),
            ("Buzz Cut", 3000, 20),
            ("Hair Styling", 2000, 15),
        ]
        connection.executemany(
            """INSERT INTO Services
               (service_name, price, duration_minutes)
               VALUES (?, ?, ?)""",
            services
        )

    # Add fictional customers
    if connection.execute("SELECT COUNT(*) FROM Customers").fetchone()[0] == 0:
        customers = [
            (f"Test Customer {i:03d}", f"TEST-{i:04d}")
            for i in range(1, 41)
        ]
        connection.executemany(
            """INSERT INTO Customers (full_name, phone)
               VALUES (?, ?)""",
            customers
        )

    # Add 120 fictional appointments
    if connection.execute("SELECT COUNT(*) FROM Appointments").fetchone()[0] == 0:
        customers = connection.execute(
            "SELECT customer_id FROM Customers"
        ).fetchall()
        barbers = connection.execute(
            "SELECT barber_id FROM Barbers"
        ).fetchall()
        services = connection.execute(
            "SELECT service_id FROM Services"
        ).fetchall()

        slots = ["09:00", "10:00", "11:00", "12:00",
                 "13:00", "14:00", "15:00", "16:00"]

        available_slots = []
        for day_offset in range(-10, 31):
            appointment_day = (
                date.today() + timedelta(days=day_offset)
            ).isoformat()

            for barber_id, in barbers:
                for slot in slots:
                    available_slots.append(
                        (barber_id, appointment_day, slot)
                    )

        random.shuffle(available_slots)
        appointments = []

        for barber_id, appointment_day, slot in available_slots[:120]:
            customer_id = random.choice(customers)[0]
            service_id = random.choice(services)[0]

            status = random.choices(
                ["Booked", "Completed", "Cancelled"],
                weights=[6, 3, 1]
            )[0]

            appointments.append((
                customer_id, barber_id, service_id,
                appointment_day, slot, status
            ))

        connection.executemany(
            """INSERT INTO Appointments
               (customer_id, barber_id, service_id,
                appointment_date, appointment_time, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            appointments
        )

    connection.commit()

    print("Database created successfully!")
    for table in ["Customers", "Barbers", "Services", "Appointments"]:
        count = connection.execute(
            f"SELECT COUNT(*) FROM {table}"
        ).fetchone()[0]
        print(f"{table}: {count} rows")

print(f"Database file: {DB_PATH}")