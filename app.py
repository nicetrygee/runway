import logging
import os
from datetime import datetime, timezone
from functools import wraps

import anthropic
from cachelib.file import FileSystemCache
from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

import capacity
import db as db_module
import recommend
from classify import ai_client, extract_task_from_text, generate_weekly_summary
from db import db  # noqa: F401 -- re-export: tests import the handle as `from app import db`
from events import events_between
from flask_session import Session

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
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Off by default so local HTTP dev (and an HTTP-only deployment) still works —
# the browser silently drops the cookie over plain HTTP if this is on. Set
# SESSION_COOKIE_SECURE=1 once the app is served over HTTPS.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("SESSION_COOKIE_SECURE") == "1"
# Filesystem sessions never expire on their own; once the count passes this
# threshold, flask-session prunes the oldest files on each new session write.
app.config["SESSION_CLIENT"] = FileSystemCache(
    os.path.join(os.path.dirname(__file__), "flask_session"), threshold=100
)
Session(app)
csrf = CSRFProtect(app)

# In-memory storage is fine for a single-process app; move to Redis if
# this ever runs as more than one worker (see #3, multi-instance scaling).
limiter = Limiter(get_remote_address, app=app)

# Kept in sync with the CHECK constraints in schema.sql.
VALID_TASK_TYPES = ["incident", "rfc", "1on1", "hiring", "delivery", "other"]
VALID_STATUSES = ["backlog", "in_progress", "blocked", "done"]

# Slice 0 additions — new `tasks` columns' enums. Kept in sync with the
# CHECK constraints in schema.sql and listed in AGENTS.md, same discipline
# as VALID_TASK_TYPES/VALID_STATUSES above. Not yet used by any route/UI —
# Slice A/B/C/D wire these into forms and validation.
VALID_STREAMS = ["task", "commitment", "delegation", "waiting"]
VALID_ITEM_TYPES = [
    "People", "Delivery", "Technical", "Stakeholder", "Strategy",
    "Hiring", "Operational", "Personal-admin",
]
VALID_PRIORITIES = ["Critical", "Important", "Normal", "Delegate", "Ignore"]
VALID_MODES = ["reactive", "proactive"]

# Weekly summary (AI) is fully wired but off by default — it's a real Claude
# call, so it stays inert until someone opts in, even with an API key set.
WEEKLY_SUMMARY_AI_ENABLED = os.environ.get("WEEKLY_SUMMARY_AI_ENABLED") == "1"


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


def validate_capture_fields(form):
    """Validate the Slice A EM-taxonomy fields on the capture (/add) form.

    Kept separate from validate_task_form so /edit (which doesn't have these
    fields) is unaffected.
    """
    errors = []

    item_type = form.get("item_type") or None
    if item_type is not None and item_type not in VALID_ITEM_TYPES:
        errors.append("Invalid item type.")

    priority = form.get("priority") or "Normal"
    if priority not in VALID_PRIORITIES:
        errors.append("Invalid priority.")

    stream = form.get("stream") or "task"
    if stream not in VALID_STREAMS:
        errors.append("Invalid stream.")

    mode = form.get("mode") or "reactive"
    if mode not in VALID_MODES:
        errors.append("Invalid mode.")

    effort_minutes = None
    raw_effort = form.get("effort_minutes")
    if raw_effort:
        try:
            effort_minutes = int(raw_effort)
            if effort_minutes not in (5, 30, 60, 120, 240, 480):
                errors.append("Invalid effort.")
        except (TypeError, ValueError):
            errors.append("Invalid effort.")

    person = form.get("person", "").strip()

    data = {
        "item_type": item_type, "priority": priority, "stream": stream,
        "mode": mode, "effort_minutes": effort_minutes, "person": person,
    }
    return data, errors


# Slice C: relationship aging/escalation. Pure functions (like
# validate_task_form above) so they're directly unit-testable without a
# request context; routes below just annotate query results with them.
# waiting/delegation items untouched this long get nudged even with no due date
STALE_DAYS_THRESHOLD = 3


def item_age_days(item, now=None):
    """Days since this item's last_touched_at."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    touched = datetime.strptime(item["last_touched_at"], "%Y-%m-%d %H:%M:%S")
    return (now - touched).days


def escalation_for_item(item, now=None):
    """A follow-up prompt for an overdue/stale waiting or delegation item,
    or None. Commitments aren't escalated the same way here — the promise
    is mine to keep, not to chase."""
    if item["stream"] not in ("waiting", "delegation"):
        return None
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    due = item["due_date"]
    if due:
        due_date = datetime.strptime(due, "%Y-%m-%d").date()
        if due_date < now.date():
            overdue_days = (now.date() - due_date).days
            return f"was due {due} ({overdue_days}d overdue) — follow up?"
    age = item_age_days(item, now)
    if age >= STALE_DAYS_THRESHOLD:
        return f"no movement in {age} days — follow up?"
    return None

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

# Assembled from Slices A–D's already-built logic: no new backend feature,
# just wiring the four North Star questions onto the landing page. Additive
# only — index()'s existing tasks/stats/kanban are untouched below.
DASHBOARD_NOW_MINUTES = 60  # default assumed availability for the glance widget


def _dashboard_context(uid):
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    candidate_items = db_module.candidate_items_for_user(uid)
    now_recs = recommend.recommend(
        candidate_items, available_minutes=DASHBOARD_NOW_MINUTES, now=now_ts, limit=3
    )

    waiting = [dict(row) for row in db_module.items_by_stream(uid, "waiting")]
    for item in waiting:
        item["age_days"] = item_age_days(item, now_ts)
        item["escalation"] = escalation_for_item(item, now_ts)
    waiting.sort(key=lambda i: (i["escalation"] is None, -i["age_days"]))
    waiting = waiting[:5]

    open_items = db_module.open_items(uid)
    neglected = capacity.neglected_items(open_items, limit=5)
    bottleneck = capacity.bottleneck_items(open_items, limit=5)

    available_hours = float(db_module.get_setting(
        uid, "available_hours", capacity.DEFAULT_AVAILABLE_HOURS))
    capacity_summary = capacity.capacity_read(open_items, available_hours)

    return {
        "now_recs": now_recs,
        "waiting": waiting,
        "neglected": neglected,
        "bottleneck": bottleneck,
        "capacity_summary": capacity_summary,
    }


# Index / Dashboard
@app.route("/")
@login_required
def index():
    uid = session["user_id"]
    tasks = db_module.tasks_for_user(uid)
    stats = {
        "total": len(tasks),
        "in_progress": sum(1 for t in tasks if t["status"] == "in_progress"),
        "blocked": sum(1 for t in tasks if t["status"] == "blocked"),
        "done": sum(1 for t in tasks if t["status"] == "done"),
        "avg_load": round(sum(t["cognitive_load"] for t in tasks) / len(tasks), 1) if tasks else 0
    }
    return render_template("index.html", tasks=tasks, stats=stats, **_dashboard_context(uid))

# Now — "what should I do right now" ranked recommendations (Slice B)
@app.route("/now")
@login_required
def now():
    uid = session["user_id"]
    try:
        available_minutes = max(1, int(request.args.get("minutes", 60)))
    except (TypeError, ValueError):
        available_minutes = 60

    items = db_module.candidate_items_for_user(uid)
    now_ts = datetime.now()
    recs = recommend.recommend(items, available_minutes=available_minutes, now=now_ts)

    if not recs:
        fitting, overflow = [], []
    elif not recs[0].fits:
        fitting, overflow = [], recs
    else:
        fitting = recs
        all_recs = recommend.recommend(
            items, available_minutes=24 * 60, now=now_ts, limit=max(len(items), 1)
        )
        fitting_ids = {r.item.id for r in fitting}
        overflow = [r for r in all_recs if r.item.id not in fitting_ids]

    return render_template(
        "now.html", fitting=fitting, overflow=overflow, available_minutes=available_minutes
    )

# Add Task
@app.route("/add", methods=["GET", "POST"])
@login_required
def add():
    if request.method == "POST":
        data, errors = validate_task_form(request.form)
        capture_data, capture_errors = validate_capture_fields(request.form)
        errors += capture_errors
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template("add.html")

        person_id = None
        if capture_data["person"]:
            person_id = db_module.get_or_create_person(session["user_id"], capture_data["person"])

        db_module.insert_task(
            session["user_id"], data["title"], data["task_type"], data["blast_radius"],
            data["sprint"], data["cognitive_load"], data["due_date"], data["notes"],
            stream=capture_data["stream"], item_type=capture_data["item_type"],
            priority=capture_data["priority"], effort_minutes=capture_data["effort_minutes"],
            mode=capture_data["mode"], person_id=person_id,
        )
        flash("Task added to Runway.", "success")
        return redirect("/")
    return render_template("add.html")

# Quick Add (AI) — freeform text in, pre-filled Add form out for review
@app.route("/quick-add", methods=["POST"])
@login_required
def quick_add():
    text = request.form.get("text", "").strip()
    if not text:
        flash("Type something to quick-add.", "error")
        return redirect("/add")
    if ai_client is None:
        flash("Quick-add isn't configured — set ANTHROPIC_API_KEY.", "error")
        return render_template("add.html")

    try:
        extracted = extract_task_from_text(text)
    except anthropic.APIStatusError:
        app.logger.exception("Quick-add extraction failed (API error)")
        flash("AI extraction failed — fill in the form below.", "error")
        return render_template("add.html")
    except Exception:
        app.logger.exception("Quick-add extraction failed")
        flash("Couldn't parse that — fill in the form below.", "error")
        return render_template("add.html")

    prefill = {
        "title": extracted.title,
        "task_type": extracted.task_type,
        "blast_radius": extracted.blast_radius,
        "sprint": extracted.sprint,
        "cognitive_load": max(1, min(5, extracted.cognitive_load)),
        "due_date": extracted.due_date,
        "notes": extracted.notes,
        "item_type": extracted.item_type,
        "priority": extracted.priority,
        "effort_minutes": extracted.effort_minutes,
        "stream": extracted.stream,
        "mode": extracted.mode,
        "person": extracted.person,
    }
    flash("Review the extracted task, then save.", "success")
    return render_template("add.html", task=prefill)

# Weekly Summary (AI) — recap of tasks completed in the last 7 days.
# The AI recap is opt-in (WEEKLY_SUMMARY_AI_ENABLED); the raw completed-task
# list always renders underneath regardless.
@app.route("/summary")
@login_required
def summary():
    uid = session["user_id"]
    tasks = db_module.completed_since(uid)

    ai_summary = None
    if WEEKLY_SUMMARY_AI_ENABLED and tasks:
        if ai_client is None:
            flash("Weekly AI summary isn't configured — set ANTHROPIC_API_KEY.", "error")
        else:
            try:
                ai_summary = generate_weekly_summary(tasks)
            except anthropic.APIStatusError:
                app.logger.exception("Weekly summary generation failed (API error)")
                flash("AI summary failed — showing the raw list below.", "error")
            except Exception:
                app.logger.exception("Weekly summary generation failed")
                flash("Couldn't generate a summary — showing the raw list below.", "error")

    return render_template("summary.html", tasks=tasks, ai_summary=ai_summary)

# --- Relationship surfaces (Slice C: commitments / delegated / waiting) ---

def _stream_view(stream):
    """Fetch + annotate one stream's items with age/escalation, and group
    them by counterparty for the templates."""
    uid = session["user_id"]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    items = []
    for row in db_module.items_by_stream(uid, stream):
        item = dict(row)
        item["age_days"] = item_age_days(item, now)
        item["escalation"] = escalation_for_item(item, now)
        items.append(item)

    grouped = {}
    for item in items:
        grouped.setdefault(item["person_name"] or "Unassigned", []).append(item)
    return items, grouped

@app.route("/commitments")
@login_required
def commitments():
    items, grouped = _stream_view("commitment")
    return render_template("commitments.html", items=items, grouped=grouped)

@app.route("/waiting")
@login_required
def waiting():
    items, grouped = _stream_view("waiting")
    return render_template("waiting.html", items=items, grouped=grouped)

@app.route("/delegated")
@login_required
def delegated():
    items, grouped = _stream_view("delegation")
    return render_template("delegated.html", items=items, grouped=grouped)

# Follow-up action — logs a followed_up event and bumps last_touched_at.
@app.route("/follow-up/<int:item_id>", methods=["POST"])
@login_required
def follow_up(item_id):
    uid = session["user_id"]
    if not db_module.get_task(item_id, uid):
        flash("Item not found.", "error")
        return redirect(request.referrer or "/")
    db_module.follow_up(item_id, uid)
    flash("Follow-up logged.", "success")
    return redirect(request.referrer or "/")

# Move an existing item onto a different stream/counterparty. Slice A owns
# capture/classification; this is the minimal additive control the slice
# doc allows so items can actually reach the commitment/delegation/waiting
# surfaces before that slice ships.
@app.route("/items/<int:item_id>/relationship", methods=["POST"])
@login_required
def set_item_relationship(item_id):
    uid = session["user_id"]
    if not db_module.get_task(item_id, uid):
        flash("Item not found.", "error")
        return redirect(request.referrer or "/")

    stream = request.form.get("stream")
    if stream not in VALID_STREAMS:
        flash("Invalid stream.", "error")
        return redirect(request.referrer or "/")

    person_id = request.form.get("person_id") or None
    if person_id is not None:
        try:
            person_id = int(person_id)
        except ValueError:
            flash("Invalid person.", "error")
            return redirect(request.referrer or "/")
        if not db_module.get_person(person_id, uid):
            flash("Invalid person.", "error")
            return redirect(request.referrer or "/")

    db_module.set_relationship(item_id, uid, stream, person_id)
    flash("Item updated.", "success")
    return redirect(request.referrer or "/")

# --- People (Slice C) ---

@app.route("/people")
@login_required
def people():
    uid = session["user_id"]
    people_list = []
    for row in db_module.people_for_user(uid):
        person = dict(row)
        person["open_count"] = len(db_module.open_items_for_person(person["id"], uid))
        people_list.append(person)
    return render_template("people.html", people=people_list)

@app.route("/people/add", methods=["POST"])
@login_required
def add_person():
    uid = session["user_id"]
    name = request.form.get("name", "").strip()
    role = request.form.get("role", "").strip()
    notes = request.form.get("notes", "").strip()
    if not name:
        flash("Name is required.", "error")
        return redirect("/people")
    db_module.create_person(uid, name, role, notes)
    flash(f"Added {name}.", "success")
    return redirect("/people")

@app.route("/people/<int:person_id>")
@login_required
def person_detail(person_id):
    uid = session["user_id"]
    rows = db_module.get_person(person_id, uid)
    if not rows:
        flash("Person not found.", "error")
        return redirect("/people")
    items = db_module.open_items_for_person(person_id, uid)
    return render_template("person_detail.html", person=rows[0], items=items)

# Weekly Review + Capacity (Slice D) — the Friday ritual: counts, a time
# breakdown, a capacity read, delegation suggestions, and a carry-forward
# reset. Aggregation logic lives in capacity.py (pure); this route only
# fetches rows and wires them together.
@app.route("/review", methods=["GET", "POST"])
@login_required
def review():
    uid = session["user_id"]

    if request.method == "POST":
        action = request.form.get("action")
        if action == "carry_forward":
            item_ids = request.form.getlist("item_id")
            for item_id in item_ids:
                db_module.carry_forward_item(int(item_id), uid)
            flash(f"Carried {len(item_ids)} item(s) into next week." if item_ids
                  else "No items selected to carry forward.",
                  "success" if item_ids else "error")
        elif action == "update_settings":
            available_hours = request.form.get("available_hours", "").strip()
            meeting_hours = request.form.get("meeting_hours_this_week", "").strip()
            try:
                if available_hours:
                    db_module.set_setting(uid, "available_hours", str(float(available_hours)))
                if meeting_hours:
                    db_module.set_setting(uid, "meeting_hours_this_week", str(float(meeting_hours)))
                flash("Settings updated.", "success")
            except ValueError:
                flash("Available hours and meeting hours must be numbers.", "error")
        return redirect("/review")

    start, end = capacity.week_bounds(datetime.now())
    completed_items = db_module.completed_between(uid, start, end)
    week_events = events_between(uid, start, end)
    open_items = db_module.open_items(uid)
    waiting_items = db_module.items_by_stream(uid, "waiting")

    available_hours = float(db_module.get_setting(
        uid, "available_hours", capacity.DEFAULT_AVAILABLE_HOURS))
    meeting_hours = float(db_module.get_setting(uid, "meeting_hours_this_week", 0))

    counts = capacity.weekly_counts(week_events, waiting_items)
    breakdown = capacity.time_breakdown(completed_items)
    strategic_pct = capacity.strategic_time_pct(breakdown["by_mode"])
    capacity_summary = capacity.capacity_read(open_items, available_hours)
    suggestions = capacity.delegation_suggestions(open_items)

    # Items already carried forward this week don't need to be re-prompted —
    # committed capacity/delegation suggestions still see the full open set.
    already_carried_ids = {
        e["item_id"] for e in week_events
        if e["event_type"] == "touched" and capacity.is_carry_forward_event(e)
    }
    carry_forward_candidates = [i for i in open_items if i["id"] not in already_carried_ids]

    ai_narrative = None
    if WEEKLY_SUMMARY_AI_ENABLED and completed_items:
        if ai_client is None:
            flash("Weekly AI summary isn't configured — set ANTHROPIC_API_KEY.", "error")
        else:
            try:
                ai_narrative = generate_weekly_summary(completed_items)
            except anthropic.APIStatusError:
                app.logger.exception("Review AI narrative failed (API error)")
                flash("AI summary failed — showing the numbers below.", "error")
            except Exception:
                app.logger.exception("Review AI narrative failed")
                flash("Couldn't generate a summary — showing the numbers below.", "error")

    return render_template(
        "review.html",
        counts=counts,
        breakdown=breakdown,
        strategic_pct=strategic_pct,
        capacity_summary=capacity_summary,
        suggestions=suggestions,
        carry_forward_candidates=carry_forward_candidates,
        available_hours=available_hours,
        meeting_hours=meeting_hours,
        ai_narrative=ai_narrative,
    )

# Edit Task
@app.route("/edit/<int:task_id>", methods=["GET", "POST"])
@login_required
def edit(task_id):
    task = db_module.get_task(task_id, session["user_id"])
    if not task:
        flash("Task not found.", "error")
        return redirect("/")
    task = task[0]

    if request.method == "POST":
        data, errors = validate_task_form(request.form, require_status=True)
        if errors:
            for error in errors:
                flash(error, "error")
            return render_template(
                "edit.html", task=task, people=db_module.people_for_user(session["user_id"])
            )

        db_module.update_task(
            task_id, session["user_id"], data["title"], data["task_type"], data["status"],
            data["blast_radius"], data["sprint"], data["cognitive_load"], data["due_date"],
            data["notes"]
        )
        flash("Task updated.", "success")
        return redirect("/")
    return render_template(
        "edit.html", task=task, people=db_module.people_for_user(session["user_id"])
    )

# Delete Task
@app.route("/delete/<int:task_id>", methods=["POST"])
@login_required
def delete(task_id):
    db_module.delete_task(task_id, session["user_id"])
    flash("Task removed.", "success")
    return redirect("/")

# Status
@app.route("/status/<int:task_id>", methods=["POST"])
@login_required
def update_status(task_id):
    new_status = (request.get_json(silent=True) or {}).get("status")
    if new_status not in VALID_STATUSES:
        return jsonify({"error": "Invalid status"}), 400
    db_module.set_status(task_id, session["user_id"], new_status)
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
        rows = db_module.get_user_by_username(username)
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
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("login.html")
        try:
            db_module.create_user(username, generate_password_hash(password))
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
