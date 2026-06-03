# Managing the database — {{app_name}}

This app uses SQLite by default (`backend/app.db`). No server, no credentials.
To use PostgreSQL instead, set `DATABASE_URL` in `.env`.

## Demo accounts (seeded on first run)
- Customer: `demo@example.com` / `demo1234`
- Admin:    `admin@example.com` / `admin1234`

## Reset to fresh demo data
    rm backend/app.db
    bash start.sh
The seed runs only when tables are empty.

## Add your own data
**Through the admin UI** — sign in as admin and use the admin pages.

**Direct edit (GUI)** — open `backend/app.db` in
[DB Browser for SQLite](https://sqlitebrowser.org/).

**Direct edit (CLI)**:
    sqlite3 backend/app.db
    sqlite> .tables
    sqlite> SELECT * FROM <table>;
    sqlite> INSERT INTO <table> (...) VALUES (...);

**Bulk seed** — edit `backend/app/seed.py`, then `rm backend/app.db && bash start.sh`.

## Switching to PostgreSQL
Set `DATABASE_URL=postgresql://USER:PASS@HOST:5432/DBNAME` in `.env`, or run
`docker compose up --build` to use the bundled Postgres container.
