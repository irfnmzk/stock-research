"""Memory layer — ticker theses, session summaries, conversation history.

Stores persistent context that survives across agent sessions.
All data in the same SQLite DB as the rest of the project.
"""

from datetime import datetime
from db import get_db


def set_thesis(db, symbol, thesis):
    """Upsert a ticker thesis. One thesis per symbol."""
    db.execute(
        """INSERT INTO ticker_thesis (symbol, thesis, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(symbol) DO UPDATE SET thesis = ?, updated_at = ?""",
        (symbol, thesis, datetime.now().isoformat(),
         thesis, datetime.now().isoformat()),
    )
    db.commit()


def get_thesis(db, symbol):
    """Get thesis for a symbol. Returns str or None."""
    row = db.execute(
        "SELECT thesis FROM ticker_thesis WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row["thesis"] if row else None


def get_all_theses(db):
    """Get all active theses."""
    rows = db.execute(
        "SELECT symbol, thesis, updated_at FROM ticker_thesis ORDER BY updated_at DESC"
    ).fetchall()
    return [{"symbol": r["symbol"], "thesis": r["thesis"], "updated_at": r["updated_at"]} for r in rows]


def delete_thesis(db, symbol):
    """Remove a ticker thesis."""
    db.execute("DELETE FROM ticker_thesis WHERE symbol = ?", (symbol,))
    db.commit()


def save_turn(db, session_id, role, content):
    """Save a conversation turn."""
    db.execute(
        """INSERT INTO conversation_turns (session_id, role, content, created_at)
           VALUES (?, ?, ?, ?)""",
        (session_id, role, content, datetime.now().isoformat()),
    )
    db.commit()


def get_turns(db, session_id):
    """Get all turns for a session."""
    rows = db.execute(
        "SELECT role, content FROM conversation_turns WHERE session_id = ? ORDER BY id",
        (session_id,),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def start_session(db):
    """Create a new session, return its ID."""
    cursor = db.execute(
        "INSERT INTO session_summary (summary, created_at) VALUES ('', ?)",
        (datetime.now().isoformat(),),
    )
    db.commit()
    return cursor.lastrowid


def save_session_summary(db, session_id, summary):
    """Update a session's summary."""
    db.execute(
        "UPDATE session_summary SET summary = ? WHERE id = ?",
        (summary, session_id),
    )
    db.commit()


def get_recent_summaries(db, n=5):
    """Get the N most recent non-empty session summaries."""
    rows = db.execute(
        """SELECT summary, created_at FROM session_summary
           WHERE summary != '' ORDER BY id DESC LIMIT ?""",
        (n,),
    ).fetchall()
    return [{"summary": r["summary"], "created_at": r["created_at"]} for r in rows]
