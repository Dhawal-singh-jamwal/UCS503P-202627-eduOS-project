import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash

import os

DATABASE = "database/eduos.db"


def get_db():
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)

    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def get_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    connection = get_db()

    connection.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            roll_number TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)

    connection.commit()
    connection.close()


def add_user(roll_number, password):
    connection = get_db()

    password_hash = generate_password_hash(password)

    connection.execute(
        """
        INSERT OR IGNORE INTO users
        (roll_number, password_hash)
        VALUES (?, ?)
        """,
        (roll_number, password_hash)
    )

    connection.commit()
    connection.close()


def authenticate_user(roll_number, password):
    connection = get_db()

    user = connection.execute(
        """
        SELECT * FROM users
        WHERE roll_number = ?
        """,
        (roll_number,)
    ).fetchone()

    connection.close()

    if user is None:
        return None

    if check_password_hash(user["password_hash"], password):
        return user

    return None
