import sqlite3
import time
from typing import Optional, Dict, Any, List, Tuple


class SyncDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            ms_user_id TEXT,
            email TEXT,
            display_name TEXT,
            access_token TEXT,
            refresh_token TEXT,
            expires_at INTEGER
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS drive_items (
            id TEXT PRIMARY KEY,
            user_id TEXT,
            name TEXT,
            folder INTEGER,
            size INTEGER,
            parent_id TEXT,
            microsoft_path TEXT,
            local_path TEXT,
            etag TEXT,
            created_at_utc TEXT,
            modified_at_utc TEXT,
            last_seen INTEGER
        )
        """)

        self.conn.commit()

    # ---------------- USERS ----------------

    def upsert_user(
        self,
        id: str,
        ms_user_id: str,
        email: str,
        display_name: str,
        access_token: str,
        refresh_token: str,
        expires_at: int
    ):
        self.conn.execute("""
        INSERT INTO users(id, ms_user_id, email, display_name, access_token, refresh_token, expires_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            expires_at=excluded.expires_at,
            display_name=excluded.display_name
        """, (id, ms_user_id, email, display_name, access_token, refresh_token, expires_at))
        self.conn.commit()

    def get_user(self, id: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM users WHERE id = ?", (id,))
        row = cur.fetchone()
        return dict(row) if row else None

    # ---------------- DRIVE ITEMS ----------------

    def has_any_items(self, user_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM drive_items WHERE user_id = ? LIMIT 1",
            (user_id,)
        )
        return cur.fetchone() is not None

    def mark_all_not_seen(self, user_id: str):
        self.conn.execute(
            "UPDATE drive_items SET last_seen = 0 WHERE user_id = ?",
            (user_id,)
        )
        self.conn.commit()

    def upsert_drive_item(self, item: Dict[str, Any], user_id: str, local_path: str) -> Optional[str]:
        now = int(time.time())

        # 1. Check existing local_path before upsert
        cur = self.conn.execute(
            "SELECT local_path FROM drive_items WHERE id = ?",
            (item["id"],)
        )
        existing_row = cur.fetchone()
        old_local_path = existing_row["local_path"] if existing_row else None

        # 2. Perform the upsert operation (as you already have it)
        self.conn.execute("""
                          INSERT INTO drive_items(id, user_id, name, folder, size, parent_id,
                                                  microsoft_path, local_path, etag,
                                                  created_at_utc, modified_at_utc, last_seen)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO
                          UPDATE SET
                              name=excluded.name,
                              folder=excluded.folder,
                              size =excluded.size,
                              parent_id=excluded.parent_id,
                              microsoft_path=excluded.microsoft_path,
                              local_path=excluded.local_path,
                              etag=excluded.etag,
                              modified_at_utc=excluded.modified_at_utc,
                              last_seen=excluded.last_seen
                          """, (
                              item["id"], user_id, item["name"], int(item["folder"]),
                              item["size"], item["parent_id"],
                              item["microsoft_path"], local_path,
                              item["etag"],
                              item["created_at_utc"],
                              item["modified_at_utc"],
                              now
                          ))
        self.conn.commit()

        # 3. Return old_local_path if it has changed
        if old_local_path and old_local_path != local_path:
            return old_local_path
        return None

    def delete_items_not_seen(self, user_id: str) -> Tuple[List[str], List[str]]:
        cur = self.conn.cursor()
        cur.execute("""
        SELECT id, local_path FROM drive_items
        WHERE user_id = ? AND last_seen = 0
        """, (user_id,))
        rows = cur.fetchall()

        ids = [r["id"] for r in rows]
        paths = [r["local_path"] for r in rows if r["local_path"]]

        self.conn.execute("""
        DELETE FROM drive_items WHERE user_id = ? AND last_seen = 0
        """, (user_id,))
        self.conn.commit()

        return ids, paths
    
        # ---------------- API HELPERS ----------------

    def list_users(self):
        cur = self.conn.execute("""
        SELECT
            u.id,
            u.email,
            u.display_name,
            u.expires_at,
            MAX(d.last_seen) AS last_sync,
            COUNT(d.id) AS item_count
        FROM users u
        LEFT JOIN drive_items d ON d.user_id = u.id
        GROUP BY u.id
        """)
        return [dict(row) for row in cur.fetchall()]
