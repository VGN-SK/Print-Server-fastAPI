import sqlite3
import bcrypt
import getpass
import re
import secrets
import string
from pathlib import Path

DB_PATH = Path("printer.db")


# ---------- Password helpers ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def is_strong_password(pw: str) -> bool:
    return (
        len(pw) >= 10 and
        re.search(r"[A-Z]", pw) and
        re.search(r"[a-z]", pw) and
        re.search(r"[0-9]", pw) and
        re.search(r"[^A-Za-z0-9]", pw)
    )


def generate_password(length=12) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    while True:
        pw = "".join(secrets.choice(chars) for _ in range(length))
        if is_strong_password(pw):
            return pw


# ---------- Main logic ----------

def main():
    if not DB_PATH.exists():
        print("❌ printer.db not found. Run the FastAPI app once to create it.")
        return

    username = input("Username: ").strip()
    if not username:
        print("❌ Username cannot be empty")
        return

    role = input("Role (user/admin) [user]: ").strip() or "user"
    if role not in ("user", "admin"):
        print("❌ Role must be 'user' or 'admin'")
        return

    choice = input("Generate password automatically? (y/n) [y]: ").strip().lower() or "y"

    if choice == "y":
        password = generate_password()
        print(f"\n🔑 Generated password for {username}: {password}")
        print("⚠️ User will be forced to change password on first login.\n")
    else:
        password = getpass.getpass("Enter password: ")
        if not is_strong_password(password):
            print("❌ Password is too weak")
            print("   Must be ≥10 chars, include upper, lower, digit, symbol")
            return

    password_hash = hash_password(password)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO users (username, password_hash, role, must_change_password)
            VALUES (?, ?, ?, 1)
        """, (username, password_hash, role))

        conn.commit()
        print(f"✅ User '{username}' added successfully")

    except sqlite3.IntegrityError:
        print("❌ Username already exists")

    finally:
        conn.close()


if __name__ == "__main__":
    main()

