"""progress.db: the student's private study state.

**Never exported**: the privacy boundary is a file, so core/bundle.py imports nothing
from here. It holds no tables yet; study progress (topic tree, quizzes) lands here.
"""

import sqlite3
from pathlib import Path


def create_progress(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()
