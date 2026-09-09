from types import SimpleNamespace

import db as db_module


def test_insert_task_writes_new_em_fields():
    user_id = db_module.create_user("kate", "hash")
    person_id = db_module.get_or_create_person(user_id, "Sarah")
    task_id = db_module.insert_task(
        user_id, "Review the Q4 hiring plan", "hiring", "", "", 2, None, "",
        stream="commitment", item_type="Hiring", priority="Important",
        effort_minutes=30, mode="reactive", person_id=person_id,
    )

    task = db_module.get_task(task_id, user_id)[0]
    assert task["stream"] == "commitment"
    assert task["item_type"] == "Hiring"
    assert task["priority"] == "Important"
    assert task["effort_minutes"] == 30
    assert task["mode"] == "reactive"
    assert task["person_id"] == person_id


def test_insert_task_defaults_new_fields_when_omitted():
    user_id = db_module.create_user("liam", "hash")
    task_id = db_module.insert_task(user_id, "Ship it", "rfc", "", "", 2, None, "")

    task = db_module.get_task(task_id, user_id)[0]
    assert task["stream"] == "task"
    assert task["priority"] == "Normal"
    assert task["mode"] == "reactive"
    assert task["item_type"] is None
    assert task["person_id"] is None


def test_add_task_with_person_creates_person_and_links(logged_in_client):
    resp = logged_in_client.post(
        "/add",
        data={
            "title": "Review the Q4 hiring plan", "task_type": "hiring",
            "cognitive_load": "2", "item_type": "Hiring", "priority": "Important",
            "effort_minutes": "30", "stream": "task", "mode": "reactive",
            "person": "Sarah",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app import db

    task = db.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1")[0]
    assert task["item_type"] == "Hiring"
    assert task["priority"] == "Important"
    assert task["effort_minutes"] == 30
    assert task["person_id"] is not None

    person = db.execute("SELECT * FROM people WHERE id = ?", task["person_id"])[0]
    assert person["name"] == "Sarah"


def test_add_task_without_person_leaves_person_id_null(logged_in_client):
    resp = logged_in_client.post(
        "/add",
        data={"title": "Solo task", "task_type": "rfc", "cognitive_load": "3"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    from app import db

    task = db.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT 1")[0]
    assert task["person_id"] is None
    assert task["stream"] == "task"
    assert task["priority"] == "Normal"


def test_add_task_invalid_item_type_rejected(logged_in_client):
    resp = logged_in_client.post(
        "/add",
        data={"title": "X", "task_type": "rfc", "cognitive_load": "3", "item_type": "Bogus"},
    )
    assert resp.status_code == 200
    assert b"Invalid item type" in resp.data

    from app import db

    assert db.execute("SELECT * FROM tasks WHERE title = 'X'") == []


def test_add_task_invalid_effort_rejected(logged_in_client):
    resp = logged_in_client.post(
        "/add",
        data={"title": "X", "task_type": "rfc", "cognitive_load": "3", "effort_minutes": "45"},
    )
    assert resp.status_code == 200
    assert b"Invalid effort" in resp.data


def test_quick_add_prefills_new_em_fields(logged_in_client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "ai_client", object())

    def fake_extract(text):
        return SimpleNamespace(
            title="Follow up with Sarah on the RFC",
            task_type="rfc", blast_radius="", sprint="",
            cognitive_load=2, due_date="", notes="",
            item_type="Technical", priority="Important",
            effort_minutes=60, stream="waiting", mode="reactive",
            person="Sarah",
        )

    monkeypatch.setattr(app_module, "extract_task_from_text", fake_extract)

    resp = logged_in_client.post("/quick-add", data={"text": "..."})

    assert resp.status_code == 200
    assert b"Sarah" in resp.data
    assert b'value="60"' in resp.data
