
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    phone TEXT NOT NULL UNIQUE,
    password_hash TEXT
);

CREATE TABLE IF NOT EXISTS Barbers (
    barber_id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    password_hash TEXT,
    work_start TEXT NOT NULL,
    work_end TEXT NOT NULL,
    CHECK (work_start < work_end)
);

CREATE TABLE IF NOT EXISTS Services (
    service_id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_name TEXT NOT NULL UNIQUE,
    price REAL NOT NULL CHECK (price >= 0),
    duration_minutes INTEGER NOT NULL
        CHECK (duration_minutes > 0)
);

CREATE TABLE IF NOT EXISTS Appointments (
    appointment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL,
    barber_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Booked'
        CHECK (status IN ('Booked', 'Completed', 'Cancelled')),
    break_group TEXT,
    -- Total length of all services of the booking (NULL = length of service_id)
    duration_minutes INTEGER CHECK (duration_minutes IS NULL OR duration_minutes > 0),

    FOREIGN KEY (customer_id)
        REFERENCES Customers(customer_id),
    FOREIGN KEY (barber_id)
        REFERENCES Barbers(barber_id),
    FOREIGN KEY (service_id)
        REFERENCES Services(service_id),

    UNIQUE (barber_id, appointment_date, appointment_time)
);

-- Every service of one booking. Appointments.service_id stays the first
-- (main) one; old bookings without rows here use Appointments.service_id.
CREATE TABLE IF NOT EXISTS AppointmentServices (
    appointment_id INTEGER NOT NULL,
    service_id INTEGER NOT NULL,
    PRIMARY KEY (appointment_id, service_id),
    FOREIGN KEY (appointment_id)
        REFERENCES Appointments(appointment_id) ON DELETE CASCADE,
    FOREIGN KEY (service_id)
        REFERENCES Services(service_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS BarberRatings (
    customer_id INTEGER NOT NULL,
    barber_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (customer_id, barber_id),
    FOREIGN KEY (customer_id)
        REFERENCES Customers(customer_id) ON DELETE CASCADE,
    FOREIGN KEY (barber_id)
        REFERENCES Barbers(barber_id) ON DELETE CASCADE
);
