"""SQLite database for property listings storage and deduplication."""
import sqlite3
import hashlib
import os
from datetime import datetime
from typing import Optional

import config


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fingerprint TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL,
            title TEXT,
            price INTEGER,
            bedrooms INTEGER,
            acres REAL,
            location TEXT,
            county TEXT,
            url TEXT,
            image_url TEXT,
            description TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dismissed INTEGER DEFAULT 0,
            starred INTEGER DEFAULT 0,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS email_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email_uid TEXT UNIQUE NOT NULL,
            sender TEXT,
            subject TEXT,
            received_at TIMESTAMP,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            listings_found INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_properties_price ON properties(price);
        CREATE INDEX IF NOT EXISTS idx_properties_county ON properties(county);
        CREATE INDEX IF NOT EXISTS idx_properties_dismissed ON properties(dismissed);
        CREATE INDEX IF NOT EXISTS idx_properties_first_seen ON properties(first_seen);
    """)
    conn.commit()
    conn.close()


def make_fingerprint(url: str, title: str, price: Optional[int]) -> str:
    """Create a dedup fingerprint from listing details."""
    # Prefer URL-based fingerprint if we have a clean listing URL
    if url and ("rightmove" in url or "zoopla" in url or "onthemarket" in url):
        # Extract property ID from URL where possible
        raw = url.strip().lower()
    else:
        # Fall back to title + price combo
        raw = f"{(title or '').strip().lower()}|{price or 0}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def upsert_property(data: dict) -> tuple[bool, int]:
    """
    Insert or update a property listing.
    Returns (is_new, property_id).
    """
    fp = make_fingerprint(data.get("url", ""), data.get("title", ""), data.get("price"))
    conn = get_connection()
    cursor = conn.cursor()

    existing = cursor.execute(
        "SELECT id FROM properties WHERE fingerprint = ?", (fp,)
    ).fetchone()

    if existing:
        cursor.execute(
            "UPDATE properties SET last_seen = ? WHERE fingerprint = ?",
            (datetime.utcnow().isoformat(), fp),
        )
        conn.commit()
        pid = existing["id"]
        conn.close()
        return False, pid

    cursor.execute(
        """INSERT INTO properties
           (fingerprint, source, title, price, bedrooms, acres, location, county, url, image_url, description)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            fp,
            data.get("source", "unknown"),
            data.get("title", ""),
            data.get("price"),
            data.get("bedrooms"),
            data.get("acres"),
            data.get("location", ""),
            data.get("county", ""),
            data.get("url", ""),
            data.get("image_url", ""),
            data.get("description", ""),
        ),
    )
    conn.commit()
    pid = cursor.lastrowid
    conn.close()
    return True, pid


def log_email(uid: str, sender: str, subject: str, received_at: str, listings_found: int):
    conn = get_connection()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO email_log (email_uid, sender, subject, received_at, listings_found)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, sender, subject, received_at, listings_found),
        )
        conn.commit()
    finally:
        conn.close()


def is_email_processed(uid: str) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT 1 FROM email_log WHERE email_uid = ?", (uid,)).fetchone()
    conn.close()
    return row is not None


def get_properties(
    show_dismissed: bool = False,
    starred_only: bool = False,
    sort_by: str = "first_seen",
    sort_dir: str = "DESC",
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    conn = get_connection()
    conditions = []
    if not show_dismissed:
        conditions.append("dismissed = 0")
    if starred_only:
        conditions.append("starred = 1")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    allowed_sorts = {"first_seen", "price", "acres", "bedrooms", "last_seen"}
    sort_col = sort_by if sort_by in allowed_sorts else "first_seen"
    direction = "ASC" if sort_dir.upper() == "ASC" else "DESC"

    rows = conn.execute(
        f"""SELECT * FROM properties {where}
            ORDER BY {sort_col} {direction}
            LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_property_count(show_dismissed: bool = False) -> int:
    conn = get_connection()
    cond = "" if show_dismissed else "WHERE dismissed = 0"
    row = conn.execute(f"SELECT COUNT(*) as cnt FROM properties {cond}").fetchone()
    conn.close()
    return row["cnt"]


def get_stats() -> dict:
    conn = get_connection()
    stats = {}
    stats["total"] = conn.execute("SELECT COUNT(*) as c FROM properties").fetchone()["c"]
    stats["active"] = conn.execute("SELECT COUNT(*) as c FROM properties WHERE dismissed = 0").fetchone()["c"]
    stats["starred"] = conn.execute("SELECT COUNT(*) as c FROM properties WHERE starred = 1").fetchone()["c"]
    stats["emails_processed"] = conn.execute("SELECT COUNT(*) as c FROM email_log").fetchone()["c"]

    row = conn.execute(
        "SELECT first_seen FROM properties ORDER BY first_seen DESC LIMIT 1"
    ).fetchone()
    stats["last_listing"] = row["first_seen"] if row else None

    row = conn.execute(
        "SELECT processed_at FROM email_log ORDER BY processed_at DESC LIMIT 1"
    ).fetchone()
    stats["last_check"] = row["processed_at"] if row else None

    conn.close()
    return stats


def toggle_property(property_id: int, field: str) -> bool:
    if field not in ("dismissed", "starred"):
        return False
    conn = get_connection()
    conn.execute(
        f"UPDATE properties SET {field} = CASE WHEN {field} = 0 THEN 1 ELSE 0 END WHERE id = ?",
        (property_id,),
    )
    conn.commit()
    conn.close()
    return True


def update_notes(property_id: int, notes: str):
    conn = get_connection()
    conn.execute("UPDATE properties SET notes = ? WHERE id = ?", (notes, property_id))
    conn.commit()
    conn.close()
