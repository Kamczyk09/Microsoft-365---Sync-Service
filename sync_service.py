#!/usr/bin/env python3
import os
import sys
import json
import time
import argparse
import requests
import msal

from sync_db import SyncDB

DB_PATH = "/opt/thalamind/db/sync.db"
BASE_ROOT = "/opt/thalamind"
CREDENTIALS_FILE = "/opt/thalamind/app_credentials.json"

with open(CREDENTIALS_FILE) as f:
    CRED = json.load(f)

CLIENT_ID = CRED["CLIENT_ID"]
TENANT_ID = CRED["TENANT_ID"]
SCOPES = CRED.get("SCOPES", ["Files.Read.All", "offline_access", "User.Read"])
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"


def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------- AUTH ----------------

class AuthManager:
    def __init__(self, db: SyncDB):
        self.db = db
        self.app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)

    def device_code_flow(self):
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        print(flow["message"])
        result = self.app.acquire_token_by_device_flow(flow)

        claims = result["id_token_claims"]
        email = claims.get("preferred_username")
        display_name = claims.get("name") or email

        expires_at = int(time.time()) + int(result["expires_in"])

        self.db.upsert_user(
            id=email,
            ms_user_id=claims.get("oid"),
            email=email,
            display_name=display_name,
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
            expires_at=expires_at
        )

        return email, display_name

    def refresh_token(self, user_id: str) -> str:
        user = self.db.get_user(user_id)
        payload = {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": user["refresh_token"],
            "scope": " ".join(SCOPES)
        }

        r = requests.post(
            f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
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
    r = requests.get(url, stream=True)
    r.raise_for_status()

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
        user = self.db.get_user(user_id)
        token = user["access_token"]
        if time.time() > user["expires_at"] - 60:
            token = self.auth.refresh_token(user_id)

        base_dir = os.path.join(BASE_ROOT, user["display_name"], "onedrive")
        os.makedirs(base_dir, exist_ok=True)

        if not self.db.has_any_items(user_id):
            log("FIRST FULL SYNC")
            self.full_sync(token, user_id, base_dir)
        else:
            log("INCREMENTAL SYNC")
            self.incremental_sync(token, user_id, base_dir)

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
    parser.add_argument("--authenticate", action="store_true")
    parser.add_argument("--sync")
    args = parser.parse_args()

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    db = SyncDB(DB_PATH)
    auth = AuthManager(db)
    core = OneDriveSyncCore(db, auth)

    if args.authenticate:
        uid, name = auth.device_code_flow()
        log(f"Authenticated {name} ({uid})")
        return

    if args.sync:
        core.sync_user(args.sync)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
