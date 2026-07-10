import logging
import os
from cs50 import SQL
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_session import Session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
from datetime import datetime

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,
)

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is not set. Set it in the environment or in a .env file "
        "before starting the app."
    )

app = Flask(__name__)
app.config["SECRET_KEY"] = SECRET_KEY
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# In-memory storage is fine for a single-process app; move to Redis if
# this ever runs as more than one worker (see #3, multi-instance scaling).
limiter = Limiter(get_remote_address, app=app)

db = SQL(os.environ.get("DATABASE_URL", "sqlite:///runway.db"))

# Kept in sync with the CHECK constraints in schema.sql.
VALID_TASK_TYPES = ["incident", "rfc", "1on1", "hiring", "delivery", "other"]
VALID_STATUSES = ["backlog", "in_progress", "blocked", "done"]

def validate_task_form(form, require_status=False):
    """Validate and coerce task form fields. Returns (data, errors)."""
    errors = []
    title = form.get("title", "").strip()
    task_type = form.get("task_type")
    blast_radius = form.get("blast_radius", "").strip()
    sprint = form.get("sprint", "").strip()
    due_date = form.get("due_date") or None
    notes = form.get("notes", "").strip()

    if not title:
        errors.append("Title is required.")
    if task_type not in VALID_TASK_TYPES:
        errors.append("Invalid task type.")

    cognitive_load = None
    try:
        cognitive_load = int(form.get("cognitive_load", 1))
        if not 1 <= cognitive_load <= 5:
            errors.append("Cognitive load must be between 1 and 5.")
    except (TypeError, ValueError):
        errors.append("Cognitive load must be a number.")

    status = None
    if require_status:
        status = form.get("status")
        if status not in VALID_STATUSES:
            errors.append("Invalid status.")

    data = {
        "title": title, "task_type": task_type, "blast_radius": blast_radius,
        "sprint": sprint, "cognitive_load": cognitive_load, "due_date": due_date,
        "notes": notes, "status": status
    }
    return data, errors

# Catch-all so unexpected errors (e.g. DB failures) never leak a stack
# trace to the client, even if --debug is left on by accident.
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unhandled exception on %s %s", request.method, request.path)
    if request.is_json:
        return jsonify({"error": "Something went wrong."}), 500
    return render_template("error.html"), 500

# Auth decorator
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("user_id") is None:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated

# Index / Dashboard
@app.route("/")
@login_required
def index():
    uid = session["user_id"]
    tasks = db.execute(
        "SELECT * FROM tasks WHERE user_id = ? ORDER BY cognitive_load DESC, due_date ASC",
        uid
    )
    stats = {
        "total": len(tasks),
        "in_progress": sum(1 for t in tasks if t["status"] == "in_progress"),
        "blocked": sum(1 for t in tasks if t["status"] == "blocked"),
        "done": sum(1 for t in tasks if t["status"] == "done"),
        "avg_load": round(sum(t["cognitive_load"] for t in tasks) / len(tasks), 1) if tasks else 0
    }
    return render_template("index.html", tasks=tasks, stats=stats)

# Add Task
@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        data, errors = validate_task_form(request.form)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("add.html")

        db.execute(
            """INSERT INTO tasks (user_id, title, task_type, blast_radius, sprint,
               cognitive_load, due_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            session["user_id"], data["title"], data["task_type"], data["blast_radius"],
            data["sprint"], data["cognitive_load"], data["due_date"], data["notes"]
        )
        flash("Task added to Runway.", "success")
        return redirect("/")
    return render_template("add.html")

# Edit Task
@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit(task_id):
    task = db.execute("SELECT * FROM tasks WHERE id = ? AND user_id = ?",
                      task_id, session["user_id"])
    if not task:
        flash("Task not found.", "error")
        return redirect("/")
    task = task[0]

    if request.method == "POST":
        data, errors = validate_task_form(request.form, require_status=True)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("edit.html", task=task)

        db.execute(
            """UPDATE tasks SET title=?, task_type=?, status=?, blast_radius=?,
               sprint=?, cognitive_load=?, due_date=?, notes=?,
               updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND user_id=?""",
            data["title"], data["task_type"], data["status"], data["blast_radius"],
            data["sprint"], data["cognitive_load"], data["due_date"], data["notes"],
            task_id, session["user_id"]
        )
        flash("Task updated.", "success")
        return redirect("/")
    return render_template("edit.html", task=task)

# Delete Task 
@app.route("/delete/<int:task_id>", methods=["POST"])
@login_required
def delete(task_id):
    db.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?",
               task_id, session["user_id"])
    flash("Task removed.", "success")
    return redirect("/")

# Status
@app.route("/status/<int:task_id>", methods=["POST"])
@login_required
def update_status(task_id):
    new_status = request.json.get("status")
    if new_status not in VALID_STATUSES:
        return jsonify({"error": "Invalid status"}), 400
    db.execute(
        "UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
        new_status, task_id, session["user_id"]
    )
    return jsonify({"ok": True})

# Login / Logout / Register
@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    # Force logout without session.clear(), which would also wipe any flash
    # message set by a redirect into this route (e.g. after registration).
    session.pop("user_id", None)
    session.pop("username", None)
    if request.method == "POST":
        username = request.form.get("username")
        rows = db.execute("SELECT * FROM users WHERE username = ?", username)
        if len(rows) != 1 or not check_password_hash(rows[0]["hash"],
                                                       request.form.get("password")):
            app.logger.warning("Failed login attempt for username=%r from %s",
                                username, request.remote_addr)
            flash("Invalid credentials.", "error")
            return render_template("login.html")
        session["user_id"] = rows[0]["id"]
        session["username"] = rows[0]["username"]
        app.logger.info("User %r logged in from %s", username, request.remote_addr)
        return redirect("/")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Username and password required.", "error")
            return render_template("login.html")
        try:
            db.execute("INSERT INTO users (username, hash) VALUES (?, ?)",
                       username, generate_password_hash(password))
        except Exception:
            app.logger.info("Registration failed (username taken): %r", username)
            flash("Username already taken.", "error")
            return render_template("login.html")
        app.logger.info("New user registered: %r", username)
        flash("Account created — log in.", "success")
        return redirect("/login")
    return render_template("login.html")

@app.route("/logout")
def logout():
    app.logger.info("User %r logged out", session.get("username"))
    session.clear()
    return redirect("/login")
