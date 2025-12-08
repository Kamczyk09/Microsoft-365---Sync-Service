# Creating a simple, dependency-free SQLite DB layer for the Microsoft 365 Sync Service.
# This uses Python's built-in sqlite3 module so it will run in any environment without extra packages.
# It implements the schema described earlier and provides helper methods for common operations.
# The code will create /mnt/data/sync.db for demo purposes. In your deployment change path to /opt/thalamind/db/sync.db

import sqlite3, os, time, json
from typing import Optional, Dict, Any, List, Tuple

DB_PATH = "/mnt/data/sync.db"    # change to /opt/thalamind/db/sync.db in production


MIGRATIONS = [
    # users
    """
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        ms_user_id TEXT,
        email TEXT,
        display_name TEXT,
        access_token TEXT,
        refresh_token TEXT,
        expires_at INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    );
    """,
    # drive_items
    """
    CREATE TABLE IF NOT EXISTS drive_items (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        folder INTEGER NOT NULL DEFAULT 0,
        size INTEGER,
        parent_id TEXT,
        microsoft_path TEXT,
        local_path TEXT,
        created_at_utc TEXT,
        created_by TEXT,
        modified_at_utc TEXT,
        modified_by TEXT,
        etag TEXT,
        last_seen INTEGER NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """,
    # permissions
    """
    CREATE TABLE IF NOT EXISTS drive_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        permission_type TEXT,
        granted_to TEXT,
        role TEXT,
        FOREIGN KEY (item_id) REFERENCES drive_items(id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """,
    # sync_state
    """
    CREATE TABLE IF NOT EXISTS sync_state (
        user_id TEXT PRIMARY KEY,
        onedrive_delta TEXT,
        sharepoint_delta TEXT,
        calendar_delta TEXT,
        teams_delta TEXT,
        last_full_sync INTEGER,
        last_incremental_sync INTEGER,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """,
    # indexes
    "CREATE INDEX IF NOT EXISTS idx_drive_items_user_id ON drive_items(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_drive_items_parent_id ON drive_items(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_drive_items_local_path ON drive_items(local_path);",
    "CREATE INDEX IF NOT EXISTS idx_permissions_item_id ON drive_permissions(item_id);"
]


class SyncDB:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        # return rows as dict-like
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _migrate(self):
        cur = self.conn.cursor()
        for sql in MIGRATIONS:
            cur.execute(sql)
        cur.close()

    def close(self):
        self.conn.close()

    # --- Users ---
    def upsert_user(self, id: str, ms_user_id: str, email: str, display_name: Optional[str],
                    access_token: Optional[str], refresh_token: Optional[str],
                    expires_at: Optional[int]):
        now = int(time.time())
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM users WHERE id = ?", (id,))
        if cur.fetchone():
            cur.execute("""
                UPDATE users SET ms_user_id=?, email=?, display_name=?, access_token=?, refresh_token=?, expires_at=?, updated_at=?
                WHERE id=?
            """, (ms_user_id, email, display_name, access_token, refresh_token, expires_at, now, id))
        else:
            cur.execute("""
                INSERT INTO users (id, ms_user_id, email, display_name, access_token, refresh_token, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (id, ms_user_id, email, display_name, access_token, refresh_token, expires_at, now, now))
        cur.close()

    def get_user(self, id: str) -> Optional[Dict[str,Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM users WHERE id = ?", (id,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None

    # --- Sync state ---
    def get_sync_state(self, user_id: str) -> Dict[str, Any]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM sync_state WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        cur.close()
        if row:
            return dict(row)
        return {"user_id": user_id, "onedrive_delta": None, "sharepoint_delta": None, "calendar_delta": None, "teams_delta": None, "last_full_sync": None, "last_incremental_sync": None}

    def set_sync_state(self, user_id: str, **fields):
        # fields may include: onedrive_delta, sharepoint_delta, calendar_delta, teams_delta, last_full_sync, last_incremental_sync
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM sync_state WHERE user_id = ?", (user_id,))
        now = int(time.time())
        if cur.fetchone():
            # build update dynamically
            updates = ", ".join(f"{k}=?" for k in fields.keys())
            params = list(fields.values()) + [user_id]
            sql = f"UPDATE sync_state SET {updates} WHERE user_id = ?"
            cur.execute(sql, params)
        else:
            # insert all (use None for missing)
            vals = { "onedrive_delta": None, "sharepoint_delta": None, "calendar_delta": None, "teams_delta": None, "last_full_sync": None, "last_incremental_sync": None }
            vals.update(fields)
            cur.execute("""
                INSERT INTO sync_state (user_id, onedrive_delta, sharepoint_delta, calendar_delta, teams_delta, last_full_sync, last_incremental_sync)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, vals["onedrive_delta"], vals["sharepoint_delta"], vals["calendar_delta"], vals["teams_delta"], vals["last_full_sync"], vals["last_incremental_sync"]))
        cur.close()

    # --- Drive items ---
    def upsert_drive_item(self, item: Dict[str, Any], user_id: str, local_path: Optional[str] = None):
        """
        item is expected to contain at least: id, name, folder(bool), size, parent_id, microsoft_path,
        created_at_utc, created_by, modified_at_utc, modified_by, etag
        """
        now = int(time.time())
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM drive_items WHERE id = ?", (item['id'],))
        last_seen = now
        if cur.fetchone():
            cur.execute("""
                UPDATE drive_items SET user_id=?, name=?, folder=?, size=?, parent_id=?, microsoft_path=?, local_path=?, created_at_utc=?, created_by=?, modified_at_utc=?, modified_by=?, etag=?, last_seen=?
                WHERE id=?
            """, (
                user_id,
                item.get('name'),
                1 if item.get('folder') else 0,
                item.get('size'),
                item.get('parent_id'),
                item.get('microsoft_path'),
                local_path,
                item.get('created_at_utc'),
                item.get('created_by'),
                item.get('modified_at_utc'),
                item.get('modified_by'),
                item.get('etag'),
                last_seen,
                item['id']
            ))
        else:
            cur.execute("""
                INSERT INTO drive_items (id, user_id, name, folder, size, parent_id, microsoft_path, local_path, created_at_utc, created_by, modified_at_utc, modified_by, etag, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item['id'],
                user_id,
                item.get('name'),
                1 if item.get('folder') else 0,
                item.get('size'),
                item.get('parent_id'),
                item.get('microsoft_path'),
                local_path,
                item.get('created_at_utc'),
                item.get('created_by'),
                item.get('modified_at_utc'),
                item.get('modified_by'),
                item.get('etag'),
                last_seen
            ))
        cur.close()

    def mark_all_not_seen(self, user_id: str):
        # utility: set last_seen to 0 before a sync; then items discovered will be updated with current timestamp.
        cur = self.conn.cursor()
        cur.execute("UPDATE drive_items SET last_seen = 0 WHERE user_id = ?", (user_id,))
        cur.close()

    def delete_items_not_seen(self, user_id: str) -> List[str]:
        # Delete items for which last_seen == 0 and return their ids
        cur = self.conn.cursor()
        cur.execute("SELECT id, local_path FROM drive_items WHERE user_id = ? AND last_seen = 0", (user_id,))
        rows = cur.fetchall()
        ids = [r['id'] for r in rows]
        local_paths = [r['local_path'] for r in rows if r['local_path']]
        cur.execute("DELETE FROM drive_items WHERE user_id = ? AND last_seen = 0", (user_id,))
        cur.close()
        return ids, local_paths

    def get_items_to_download(self, user_id: str) -> List[Dict[str, Any]]:
        # For demo: return all file items (folder=0)
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM drive_items WHERE user_id = ? AND folder = 0", (user_id,))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]

    # --- Permissions ---
    def replace_permissions_for_item(self, item_id: str, user_id: str, permissions: List[Dict[str,Any]]):
        # simple approach: delete existing for that item/user and insert new
        cur = self.conn.cursor()
        cur.execute("DELETE FROM drive_permissions WHERE item_id = ? AND user_id = ?", (item_id, user_id))
        for p in permissions:
            cur.execute("""INSERT INTO drive_permissions (item_id, user_id, permission_type, granted_to, role) VALUES (?, ?, ?, ?, ?)""",
                        (item_id, user_id, p.get('permission_type'), p.get('granted_to'), p.get('role')))
        cur.close()

    def list_permissions(self, item_id: str) -> List[Dict[str,Any]]:
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM drive_permissions WHERE item_id = ?", (item_id,))
        rows = cur.fetchall()
        cur.close()
        return [dict(r) for r in rows]

# ------------------ Demo run to show DB created and tables ------------------
if __name__ == "__main__":
    db = SyncDB(DB_PATH)
    print("Database initialized at", DB_PATH)
    cur = db.conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    tables = [r[0] for r in cur.fetchall()]
    print("Tables:", tables)
    # insert a demo user and a demo drive item
    demo_user_id = "alice-local-1"
    db.upsert_user(id=demo_user_id, ms_user_id="ms-guid-123", email="alice@example.com", display_name="Alice", access_token="redacted", refresh_token="redacted", expires_at=int(time.time())+3600)
    db.set_sync_state(demo_user_id, onedrive_delta="deltoken-demo", last_full_sync=int(time.time()))
    db.upsert_drive_item({
        "id": "item-1",
        "name": "hello.txt",
        "folder": False,
        "size": 123,
        "parent_id": None,
        "microsoft_path": "/drive/root:/hello.txt",
        "created_at_utc": "2025-12-01T12:00:00Z",
        "created_by": "alice@example.com",
        "modified_at_utc": "2025-12-03T12:00:00Z",
        "modified_by": "alice@example.com",
        "etag": "W/\"abc123\""
    }, user_id=demo_user_id, local_path=f"/opt/thalamind/{demo_user_id}/onedrive/hello.txt")
    # show inserted rows
    cur.execute("SELECT id, name, local_path, etag FROM drive_items WHERE user_id = ?", (demo_user_id,))
    print("Drive items for demo user:", cur.fetchall())
    db.close()

print("\nModule run complete. You can reuse SyncDB in your sync code (import SyncDB from this file).")

