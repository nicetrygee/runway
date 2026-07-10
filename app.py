import os
from cs50 import SQL
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, session, jsonify
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
from datetime import datetime

load_dotenv()

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

db = SQL("sqlite:///runway.db")

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
        title = request.form.get("title", "").strip()
        task_type = request.form.get("task_type")
        blast_radius = request.form.get("blast_radius", "").strip()
        sprint = request.form.get("sprint", "").strip()
        cognitive_load = int(request.form.get("cognitive_load", 1))
        due_date = request.form.get("due_date") or None
        notes = request.form.get("notes", "").strip()

        if not title or not task_type:
            flash("Title and task type are required.", "error")
            return render_template("add.html")

        db.execute(
            """INSERT INTO tasks (user_id, title, task_type, blast_radius, sprint,
               cognitive_load, due_date, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            session["user_id"], title, task_type, blast_radius,
            sprint, cognitive_load, due_date, notes
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
        db.execute(
            """UPDATE tasks SET title=?, task_type=?, status=?, blast_radius=?,
               sprint=?, cognitive_load=?, due_date=?, notes=?,
               updated_at=CURRENT_TIMESTAMP
               WHERE id=? AND user_id=?""",
            request.form.get("title"), request.form.get("task_type"),
            request.form.get("status"), request.form.get("blast_radius"),
            request.form.get("sprint"), int(request.form.get("cognitive_load", 1)),
            request.form.get("due_date") or None, request.form.get("notes"),
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
    valid = ["backlog", "in_progress", "blocked", "done"]
    if new_status not in valid:
        return jsonify({"error": "Invalid status"}), 400
    db.execute(
        "UPDATE tasks SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
        new_status, task_id, session["user_id"]
    )
    return jsonify({"ok": True})

# Login / Logout / Register 
@app.route("/login", methods=["GET", "POST"])
def login():
    session.clear()
    if request.method == "POST":
        rows = db.execute("SELECT * FROM users WHERE username = ?",
                          request.form.get("username"))
        if len(rows) != 1 or not check_password_hash(rows[0]["hash"],
                                                       request.form.get("password")):
            flash("Invalid credentials.", "error")
            return render_template("login.html")
        session["user_id"] = rows[0]["id"]
        session["username"] = rows[0]["username"]
        return redirect("/")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
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
            flash("Username already taken.", "error")
            return render_template("login.html")
        flash("Account created — log in.", "success")
        return redirect("/login")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")
