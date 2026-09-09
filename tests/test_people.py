import db as db_module


def test_get_or_create_person_creates_new_person():
    user_id = db_module.create_user("gina", "hash")
    person_id = db_module.get_or_create_person(user_id, "Sarah")

    rows = db_module.db.execute("SELECT * FROM people WHERE id = ?", person_id)
    assert len(rows) == 1
    assert rows[0]["name"] == "Sarah"
    assert rows[0]["user_id"] == user_id


def test_get_or_create_person_reuses_existing():
    user_id = db_module.create_user("henry", "hash")
    first_id = db_module.get_or_create_person(user_id, "Sarah")
    second_id = db_module.get_or_create_person(user_id, "Sarah")

    assert first_id == second_id
    rows = db_module.db.execute("SELECT * FROM people WHERE user_id = ?", user_id)
    assert len(rows) == 1


def test_get_or_create_person_scoped_per_user():
    user1 = db_module.create_user("ivan", "hash")
    user2 = db_module.create_user("jill", "hash")
    id1 = db_module.get_or_create_person(user1, "Sarah")
    id2 = db_module.get_or_create_person(user2, "Sarah")

    assert id1 != id2


def test_get_or_create_person_strips_whitespace():
    user_id = db_module.create_user("kyle", "hash")
    id1 = db_module.get_or_create_person(user_id, "Sarah")
    id2 = db_module.get_or_create_person(user_id, "  Sarah  ")

    assert id1 == id2
