import aiosqlite
import uuid
from datetime import datetime, timezone

DB_PATH = "jobs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    language TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    error TEXT
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

async def create_job(language: str) -> str:
    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO jobs (id, status, language, created_at) VALUES (?, ?, ?, ?)",
            (job_id, "queued", language, now),
        )
        await db.commit()
    return job_id

async def mark_running(job_id: str):
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (now, job_id),
        )
        await db.commit()

async def mark_done(job_id: str, exit_code: int | None, stdout: str, stderr: str, error: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    status = "succeeded" if (exit_code == 0 and not error) else "failed"
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """UPDATE jobs
               SET status=?, finished_at=?, exit_code=?, stdout=?, stderr=?, error=?
               WHERE id=?""",
            (status, now, exit_code, stdout, stderr, error, job_id),
        )
        await db.commit()

async def get_job(job_id: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None