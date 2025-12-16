#!/usr/bin/env python3
import os
import sys
import json
import time
import argparse
import requests

# ===== path fixes: resolve paths relative to the repository root (./project) =====
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# repo root is two levels up from src/ (./project)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))

# ensure local imports work regardless of current working directory
sys.path.insert(0, SCRIPT_DIR)

# Import SyncDB from local module
from sync_db import SyncDB

DB_PATH = "/project/backend/db/sync.db"
BASE_ROOT = "/opt/thalamind"

# app_credentials.json lives inside the backend directory per your note
CREDENTIALS_FILE = os.path.join(PROJECT_ROOT, "backend", "app_credentials.json")
# =====================================================================

with open(CREDENTIALS_FILE) as f:
    CRED = json.load(f)

CLIENT_ID = CRED["CLIENT_ID"]
CLIENT_SECRET = CRED["CLIENT_SECRET"]
TENANT_ID = CRED["TENANT_ID"]
SCOPES = CRED.get("SCOPES", ["Files.Read.All", "offline_access", "User.Read"])
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"


def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------- AUTH ----------------

class AuthManager:
    def __init__(self, db: SyncDB):
        self.db = db
        self.CLIENT_ID = CLIENT_ID
        self.CLIENT_SECRET = CLIENT_SECRET
        self.TENANT_ID = TENANT_ID
        self.SCOPES = SCOPES

    def refresh_token(self, user_id: str) -> str:
        user = self.db.get_user(user_id)

        payload = {
            "client_id": self.CLIENT_ID,
            "client_secret": self.CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": user["refresh_token"],
            "scope": " ".join(self.SCOPES)
        }

        r = requests.post(
            f"https://login.microsoftonline.com/{self.TENANT_ID}/oauth2/v2.0/token",
            data=payload
        )
        r.raise_for_status()

        data = r.json()
        expires_at = int(time.time()) + int(data["expires_in"])

        self.db.upsert_user(
            id=user_id,
            ms_user_id=user["ms_user_id"],
            email=user["email"],
            display_name=user["display_name"],
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", user["refresh_token"]),
            expires_at=expires_at
        )

        return data["access_token"]



# ---------------- GRAPH ----------------

def graph_get(token, url):
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()


def list_recursive(token, item_id="root"):
    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/children"
    data = graph_get(token, url)
    items = []

    for it in data["value"]:
        items.append(it)
        if "folder" in it:
            items.extend(list_recursive(token, it["id"]))

    return items


def local_path(base, item):
    pr = item.get("parentReference", {}).get("path", "")
    pr = pr.replace("/drive/root:", "").strip("/")
    return os.path.join(base, pr, item["name"])


def download(token, item, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

    url = item["@microsoft.graph.downloadUrl"]
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()   # <- this is the key line

    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        for c in r.iter_content(65536):
            f.write(c)

    os.replace(tmp, path)



def safe_local_remove(path: str):
    """Safely removes a file or an empty directory, logging the action."""
    if not path:
        return

    if os.path.isdir(path):
        try:
            os.rmdir(path)
            log(f"REMOVED empty folder: {path}")
        except OSError:
            # os.rmdir will fail if the directory is not empty. This is acceptable
            # as non-empty folders will be removed when all their contents are gone.
            # We'll rely on the deletion of individual items to empty the folder.
            pass
    elif os.path.exists(path):
        os.remove(path)
        log(f"REMOVED file: {path}")


# ---------------- SYNC CORE ----------------

class OneDriveSyncCore:
    def __init__(self, db: SyncDB, auth: AuthManager):
        self.db = db
        self.auth = auth

    def sync_user(self, user_id: str):

        status = self.db.get_sync_status(user_id)
        if status and status["state"] == "running":
            log("Sync already running, skipping")
            return

        self.db.set_sync_state(user_id, "running")

        try:
            user = self.db.get_user(user_id)
            token = user["access_token"]

            if time.time() > user["expires_at"] - 60:
                token = self.auth.refresh_token(user_id)

            base_dir = os.path.join(BASE_ROOT, user["ms_user_id"], "onedrive")
            os.makedirs(base_dir, exist_ok=True)

            if not self.db.has_any_items(user_id):
                self.full_sync(token, user_id, base_dir)
            else:
                self.incremental_sync(token, user_id, base_dir)

            self.db.set_sync_state(user_id, "idle")

        except requests.HTTPError as e:
            # 🔴 THIS IS THE PART YOU WERE ASKING ABOUT
            if e.response is not None and e.response.status_code == 401:
                self.db.set_sync_state(
                    user_id,
                    "error",
                    "Token expired, revoked, or consent removed"
                )
            else:
                self.db.set_sync_state(user_id, "error", str(e))
            raise

        except Exception as e:
            self.db.set_sync_state(user_id, "error", str(e))
            raise

    def full_sync(self, token, user_id, base_dir):
        # 1. Prepare for detection of deleted items
        self.db.mark_all_not_seen(user_id)
        items = list_recursive(token)

        for it in items:
            lp = local_path(base_dir, it)

            # 2. Upsert the item and capture the old path for move/rename detection
            old_lp = self.db.upsert_drive_item(
                # ... same arguments as before
                {
                    "id": it["id"],
                    "name": it["name"],
                    "folder": "folder" in it,
                    "size": it.get("size"),
                    "parent_id": it.get("parentReference", {}).get("id"),
                    "microsoft_path": it.get("parentReference", {}).get("path"),
                    "etag": it.get("eTag"),
                    "created_at_utc": it.get("createdDateTime"),
                    "modified_at_utc": it.get("lastModifiedDateTime")
                }, user_id, lp)

            # 3. Handle move/rename cleanup (delete item from old location)
            if old_lp:
                log(f"RENAMED/MOVED: {old_lp} -> {lp}")
                safe_local_remove(old_lp)

            # 4. Handle file download (to the *new* location)
            if "file" in it:
                download(token, it, lp)

        # 5. Handle deletion cleanup (items not seen in the current listing)
        deleted_ids, deleted_paths = self.db.delete_items_not_seen(user_id)

        # 6. Remove the files/folders from the local file system
        # Process paths in reverse order for better chances of deleting empty folders first.
        for p in reversed(deleted_paths):
            safe_local_remove(p)

        if deleted_ids:
            log(f"DELETED {len(deleted_ids)} items from DB and local storage.")

    def incremental_sync(self, token, user_id, base_dir):
        self.full_sync(token, user_id, base_dir)


# ---------------- CLI ----------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sync")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    db = SyncDB(DB_PATH)
    auth = AuthManager(db)
    core = OneDriveSyncCore(db, auth)

    if args.sync:
        core.sync_user(args.sync)
        return

    parser.print_help()



if __name__ == "__main__":
    main()