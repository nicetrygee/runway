CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    task_type TEXT NOT NULL CHECK(task_type IN ('incident','rfc','1on1','hiring','delivery','other')),
    status TEXT NOT NULL DEFAULT 'backlog' CHECK(status IN ('backlog','in_progress','blocked','done')),
    blast_radius TEXT,
    sprint TEXT,
    cognitive_load INTEGER DEFAULT 1 CHECK(cognitive_load BETWEEN 1 AND 5),
    due_date TEXT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
