from typing import Dict
from fastapi import FastAPI, UploadFile, File, Header, HTTPException, Depends, Form, status
from queue import Queue
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from datetime import datetime, timedelta, timezone

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from PyPDF2 import PdfReader

import shutil
import os
import time
import threading
import sqlite3
import bcrypt
import re
import uuid
import cups


print_queue = Queue()

cups_conn = cups.Connection()
PRINTER_NAME = "HP-LaserJet-1020"

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

JOB_QUEUED = "queued"
JOB_PRINTING = "printing"
JOB_COMPLETED = "completed"
JOB_FAILED = "failed"
JOB_CANCELLED = "cancelled"


DB_PATH = "printer.db"
MONTHLY_PAPER_QUOTA = 10

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


jobs_lock = threading.Lock()
jobs: Dict[int, dict] = {}

tokens = {}
security = HTTPBearer()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS print_jobs (
        job_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        filename TEXT NOT NULL,
        file_path TEXT NOT NULL,
        papers INTEGER NOT NULL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    )
""")
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL,
        must_change_password INTEGER NOT NULL DEFAULT 1
    )
    """)


    conn.commit()
    conn.close()

def load_pending_jobs():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT job_id, status, filename, file_path, cancel_requested
        FROM print_jobs
        WHERE status IN (?, ?)
    """, (JOB_QUEUED, JOB_PRINTING))

    rows = cursor.fetchall()
    conn.close()

    with jobs_lock:
        for r in rows:
            jobs[r[0]] = {
                "job_id": r[0],
                "status": r[1],
                "filename": r[2],
                "file_path": r[3],
                "cancel_requested": bool(r[4])
            }
            if r[1] == JOB_QUEUED:
                print_queue.put(r[0])
                
def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials  # this is the actual token string

    user = tokens.get(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")

    return user

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
    
def is_strong_password(pw: str) -> bool:
    return (
        len(pw) >= 10 and
        re.search(r"[A-Z]", pw) and
        re.search(r"[a-z]", pw) and
        re.search(r"[0-9]", pw) and
        re.search(r"[^A-Za-z0-9]", pw)
    )

def require_password_change_complete(
user=Depends(get_current_user)
):
    if user["must_change_password"]:
        raise HTTPException(
            status_code=403,
            detail="Password change required"
        )
    return user
  
def count_pdf_pages(file_path: str) -> int:
    try:
        with open(file_path, "rb") as f:
            reader = PdfReader(f)
            return len(reader.pages)
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid or corrupted PDF file"
        )
    
def get_monthly_paper_usage(user_id: int) -> int:
    start_utc, end_utc = get_current_ist_month_window()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COALESCE(SUM(papers), 0)
        FROM print_jobs
        WHERE user_id = ?
        AND status IN ('queued', 'printing', 'completed')
        AND created_at >= ?
        AND created_at < ?
    """, (user_id, start_utc, end_utc))

    used_papers = cursor.fetchone()[0]
    conn.close()

    return used_papers

def calculate_papers(pages: int, copies: int, sides: str) -> int:
    if sides == "two-sided-long-edge":
        papers_per_copy = (pages + 1) // 2
    else:
        papers_per_copy = pages

    return papers_per_copy * copies


def get_current_ist_month_window():
    # Current time in IST
    now_ist = datetime.now(IST)

    # First day of this month at 00:00 IST
    start_ist = now_ist.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    # First day of next month
    if start_ist.month == 12:
        next_month_ist = start_ist.replace(
            year=start_ist.year + 1, month=1
        )
    else:
        next_month_ist = start_ist.replace(
            month=start_ist.month + 1
        )

    # Convert both to UTC
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = next_month_ist.astimezone(timezone.utc)

    return start_utc.isoformat(), end_utc.isoformat()


def create_default_admin():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM users WHERE role = 'admin'")
    admin_exists = cursor.fetchone()

    if not admin_exists:
        cursor.execute("""
            INSERT INTO users (username, password_hash, role)
            VALUES (?, ?, ?)
        """, (
            "admin",
            hash_password("admin123"),
            "admin"
        ))
        conn.commit()

    conn.close()

    
init_db()
load_pending_jobs()
create_default_admin()
    
def insert_job(user_id, status, filename, file_path, papers):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO print_jobs (user_id, status, filename, file_path ,papers ,cancel_requested ,created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
""", (
    user_id,
    status,
    filename,
    file_path,
    papers,
    0,
    datetime.now(timezone.utc).isoformat()

))


    job_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return job_id


def update_job_status(job_id, status):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE print_jobs
        SET status = ?
        WHERE job_id = ?
    """, (status, job_id))

    conn.commit()
    conn.close()

def get_job_from_db(job_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT job_id, status, filename, file_path, cancel_requested, created_at
        FROM print_jobs
        WHERE job_id = ?
    """, (job_id,))

    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    return {
        "job_id": row[0],
        "status": row[1],
        "filename": row[2],
        "file_path": row[3],
        "cancel_requested": bool(row[4]),
        "created_at": row[5]
    }
    

def set_cancel_requested(job_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE print_jobs
        SET cancel_requested = 1
        WHERE job_id = ?
    """, (job_id,))

    conn.commit()
    conn.close()

def require_admin(user=Depends(get_current_user)):
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def get_printer_capabilities(printer_name: str):
    conn = cups.Connection()
    printers = conn.getPrinters()

    if printer_name not in printers:
        raise RuntimeError("Printer not found")

    attrs = printers[printer_name]

    # Duplex support
    duplex = "sides-supported" in attrs and len(attrs["sides-supported"]) > 1

    # Color support
    color = (
        "color-supported" in attrs and attrs["color-supported"]
    ) or (
        "ColorModel" in attrs
    )

    # Max copies (fallback to 1 if unknown)
    max_copies = int(attrs.get("copies-supported", "1").split("-")[-1])

    return {
        "duplex": duplex,
        "color": color,
        "max_copies": max_copies
    }

def get_printer_status(printer_name: str):
    conn = cups.Connection()
    printers = conn.getPrinters()

    if printer_name not in printers:
        return {
            "status": "offline",
            "reason": "Printer not found"
        }

    attrs = printers[printer_name]
    state = attrs.get("printer-state")
    reasons = attrs.get("printer-state-reasons", [])

    if state == 3:
        status = "idle"
    elif state == 4:
        status = "printing"
    else:
        status = "offline"

    return {
        "status": status,
        "reasons": reasons
    }


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request}
    )

@app.get("/print", response_class=HTMLResponse)
def print_page(request: Request):
    return templates.TemplateResponse(
        "print.html",
        {"request": request}
    )
    
@app.get("/change-password", response_class=HTMLResponse)
def change_password_page(request: Request):
    return templates.TemplateResponse(
        "change_password.html",
        {"request": request}
    )

@app.get("/quota")
def get_quota(user=Depends(get_current_user)):
    used = get_monthly_paper_usage(user["user_id"])
    return {
        "used": used,
        "limit": MONTHLY_PAPER_QUOTA
    }
    
@app.get("/printer/capabilities")
def printer_capabilities(user=Depends(get_current_user)):
    try:
        return get_printer_capabilities(PRINTER_NAME)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/printer/status")
def printer_status(user=Depends(get_current_user)):
    return get_printer_status(PRINTER_NAME)
    
    
@app.post("/login")
def login(
    username: str = Form(...),
    password: str = Form(...)
):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT user_id, password_hash, role, must_change_password
        FROM users
        WHERE username = ?
    """, (username,))

    row = cursor.fetchone()
    conn.close()

    # ❌ Invalid username OR password
    if not row or not verify_password(password, row[1]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    # ✅ Valid login
    token = str(uuid.uuid4())

    tokens[token] = {
        "user_id": row[0],
        "username": username,
        "role": row[2],
        "must_change_password": row[3],
        "token": token
    }

    return {
        "token": token,
        "role": row[2],
        "must_change_password": row[3]
    }

    
@app.post("/print")
def submit_print(
    file: UploadFile = File(...),
    copies: int = Form(1),
    color_mode: str = Form("bw"),
    sides: str = Form("one-sided"),
    user=Depends(require_password_change_complete)
):
    
    if copies < 1 or copies > 50:
        raise HTTPException(status_code=400, detail="Invalid number of copies")

    if color_mode not in ("bw", "color"):
        raise HTTPException(status_code=400, detail="Invalid color mode")

    if sides not in ("one-sided", "two-sided-long-edge"):
        raise HTTPException(status_code=400, detail="Invalid sides option")

    # Admins are exempt
    if user["role"] != "admin":
        file_path = os.path.join(UPLOAD_DIR, file.filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        pdf_pages = count_pdf_pages(file_path)

        papers = calculate_papers(
            pages=pdf_pages,
            copies=copies,
            sides=sides
        )

        used_papers = get_monthly_paper_usage(user["user_id"])

        if used_papers + papers > MONTHLY_PAPER_QUOTA:
            os.remove(file_path)
            raise HTTPException(
                status_code=403,
                detail=(
                f"Monthly paper quota exceeded. "
                f"Used {used_papers}/{MONTHLY_PAPER_QUOTA} papers."
                )
            )

    else:
        file_path = os.path.join(UPLOAD_DIR, file.filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        pdf_pages = count_pdf_pages(file_path)
        papers = calculate_papers(
            pages=pdf_pages,
            copies=copies,
            sides=sides
        )
        
    # Insert job
    job_id = insert_job(
        user_id=user["user_id"],
        status=JOB_QUEUED,
        filename=file.filename,
        file_path=file_path,
        papers=papers
    )
    # Cache in memory
    with jobs_lock:
        jobs[job_id] = {
            "job_id": job_id,
            "user_id": user["user_id"],
            "status": JOB_QUEUED,
            "filename": file.filename,
            "file_path": file_path,
            "papers": papers,
            "copies": copies,
            "color_mode": color_mode,
            "sides": sides,
            "cancel_requested": False
        }



    # Enqueue
    print_queue.put(job_id)

    return jobs[job_id]

@app.post("/change-password")
def change_password(
    old_password: str = Form(...),
    new_password: str = Form(...),
    user=Depends(get_current_user)
):
    
    if old_password == new_password:
        raise HTTPException(
            status_code=400,
            detail="New password must be different from old password"
        )

    if not is_strong_password(new_password):
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 10 characters and include uppercase, lowercase, number, and symbol."
        )

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute(
        "SELECT password_hash FROM users WHERE user_id = ?",
        (user["user_id"],)
    )
    stored_hash = cursor.fetchone()[0]

    if not verify_password(old_password, stored_hash):
        conn.close()
        raise HTTPException(status_code=401, detail="Old password incorrect")

    cursor.execute("""
        UPDATE users
        SET password_hash = ?, must_change_password = 0
        WHERE user_id = ?
    """, (
        hash_password(new_password),
        user["user_id"]
    ))

    conn.commit()
    conn.close()
    
    token = user["token"]
    if token in tokens:
        tokens[token]["must_change_password"] = 0
        
    return {"message": "Password updated successfully"}

@app.get("/job/{job_id}")
def job_status(job_id: int):
    job = get_job_from_db(job_id)

    if not job:
        return {"error": "Job not found"}

    return job
    
@app.get("/jobs")
def my_jobs(user=Depends(require_password_change_complete)):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
    SELECT job_id, filename, status, papers, created_at
    FROM print_jobs
    WHERE user_id = ?
    ORDER BY job_id DESC
""", (user["user_id"],))


    rows = cursor.fetchall()
    conn.close()

    return [
    {
        "job_id": r[0],
        "filename": r[1],
        "status": r[2],
        "papers": r[3],
        "created_at": r[4]
    }
    for r in rows
]


    
    
@app.post("/admin/job/{job_id}/cancel")
def cancel_job(job_id: int, admin=Depends(require_admin)):
    with jobs_lock:
        job = jobs.get(job_id)

        if not job:
            return {"error": "Job not found"}

        if job["status"] == JOB_QUEUED:
            job["status"] = JOB_CANCELLED
            job["cancel_requested"] = True
            update_job_status(job_id, JOB_CANCELLED)
            set_cancel_requested(job_id)
            return {"message": "Job cancelled (queued)"}

        if job["status"] == JOB_PRINTING:
            job["cancel_requested"] = True
            set_cancel_requested(job_id)
            return {"message": "Cancel requested (printing)"}

        return {"error": f"Cannot cancel job in state '{job['status']}'"}

        
@app.get("/admin/jobs")
def list_all_jobs(admin=Depends(require_admin)):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT job_id, status, filename, papers, created_at
        FROM print_jobs
        ORDER BY job_id DESC
    """)

    rows = cursor.fetchall()
    conn.close()

    return [
        {
            "job_id": r[0],
            "status": r[1],
            "filename": r[2],
            "papers":r[3],
            "created_at": r[4]
        }
        for r in rows
    ]   
    
def print_worker():
    while True:
        job_id = print_queue.get()

        with jobs_lock:
            job = jobs.get(job_id)

            if not job or job["status"] == JOB_CANCELLED:
                print_queue.task_done()
                continue

            job["status"] = JOB_PRINTING
            update_job_status(job_id, JOB_PRINTING)

        try:
            print(f"Sending job {job_id} to CUPS")

    # 1️⃣ Submit job to CUPS
            cups_options = {
                "copies": str(job["copies"]),
                "sides": job["sides"]
            }

            if job["color_mode"] == "bw":
                cups_options["ColorModel"] = "Gray"
            else:
                cups_options["ColorModel"] = "RGB"

            cups_job_id = cups_conn.printFile(
                PRINTER_NAME,
                job["file_path"],
                f"PrintJob-{job_id}",
                cups_options
            )


            print(f"CUPS job id: {cups_job_id}")

    # 2️⃣ Poll CUPS job state
            while True:
                time.sleep(1)

        # Fetch job info from CUPS
                cups_jobs = cups_conn.getJobs(which_jobs="not-completed")

        # 3️⃣ Check cancellation request (from DB)
                with jobs_lock:
                    db_job = get_job_from_db(job_id)
                    if db_job and db_job["cancel_requested"]:
                        print(f"Cancelling CUPS job {cups_job_id}")
                        cups_conn.cancelJob(PRINTER_NAME, cups_job_id)

                        job["status"] = JOB_CANCELLED
                        update_job_status(job_id, JOB_CANCELLED)
                        break

        # 4️⃣ Check if CUPS job is done
                if cups_job_id not in cups_jobs:
                    with jobs_lock:
                        job["status"] = JOB_COMPLETED
                        update_job_status(job_id, JOB_COMPLETED)
                        print(f"Job {job_id} completed")
                    break


        except Exception as e:
            with jobs_lock:
                job["status"] = JOB_FAILED
                update_job_status(job_id, JOB_FAILED)
                print(f"Job {job_id} failed:", e)

        finally:
            print_queue.task_done()


worker_thread = threading.Thread(target=print_worker, daemon=True)
worker_thread.start()
