#!/usr/bin/env python3
"""
sync_service.py

- Uses SyncDB (sqlite) in /opt/thalamind/db/sync.db
- Auth: device code flow (msal) via --authenticate
- Sync: python sync_service.py --sync <user_email>
- Behavior:
    * If local base dir or metadata.json missing => perform full scan & download
    * Otherwise perform incremental scan (recursive listing) and update files
    * Persist items into SQLite (drive_items) with etag and last_seen
"""

import os
import sys
import json
import time
import argparse
import requests
import msal

from sync_db import SyncDB  # ensure this file is at /opt/thalamind/sync_db.py or in same dir

# Configuration
DB_PATH = "/opt/thalamind/db/sync.db"
CREDENTIALS_FILE = "/opt/thalamind/app_credentials.json"
BASE_ROOT = "/opt/thalamind"  # per-user root -> BASE_ROOT/{user}/onedrive

# Load credentials
if not os.path.exists(CREDENTIALS_FILE):
    print(f"[ERROR] Missing credentials file {CREDENTIALS_FILE}")
    sys.exit(1)
with open(CREDENTIALS_FILE, "r") as f:
    CRED = json.load(f)

CLIENT_ID = CRED.get("CLIENT_ID")
TENANT_ID = CRED.get("TENANT_ID")
SCOPES = CRED.get("SCOPES") or ["Files.Read.All", "offline_access", "User.Read"]
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

# Logging helper
def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# ---------------------------
# Auth manager
# ---------------------------
class AuthManager:
    def __init__(self, db: SyncDB):
        self.db = db
        self.app = msal.PublicClientApplication(client_id=CLIENT_ID, authority=AUTHORITY)

    def device_code_flow(self):
        flow = self.app.initiate_device_flow(scopes=SCOPES)
        if "message" in flow:
            print(flow["message"])
        else:
            raise RuntimeError("Failed to start device code flow.")
        result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Auth failed: {result}")
        # id token claims give user identity
        claims = result.get("id_token_claims", {}) or {}
        ms_user_id = claims.get("oid") or claims.get("sub") or result.get("account", {}).get("home_account_id", "unknown")
        email = claims.get("preferred_username") or claims.get("upn") or claims.get("email") or ms_user_id
        display_name = claims.get("name") or email
        # choose local user id mapping - use email (safe)
        local_user_id = email
        # store in DB (tokens stored as-is; consider encrypting in production)
        expires_in = result.get("expires_in")
        expires_at = int(time.time()) + int(expires_in) if expires_in else None
        self.db.upsert_user(
            id=local_user_id,
            ms_user_id=ms_user_id,
            email=email,
            display_name=display_name,
            access_token=result.get("access_token"),
            refresh_token=result.get("refresh_token"),
            expires_at=expires_at
        )
        log(f"Authenticated and stored user {local_user_id}")
        return local_user_id, display_name

    def refresh_access_token(self, user_id: str):
        user = self.db.get_user(user_id)
        if not user:
            log(f"No user {user_id} in DB to refresh token")
            return None
        refresh_token = user.get("refresh_token")
        if not refresh_token:
            log("No refresh token available.")
            return None
        token_url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
        payload = {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join(SCOPES)
        }
        r = requests.post(token_url, data=payload)
        if r.status_code != 200:
            log(f"Refresh failed: {r.status_code} {r.text}")
            return None
        jr = r.json()
        access = jr.get("access_token")
        new_refresh = jr.get("refresh_token", refresh_token)
        expires_in = jr.get("expires_in")
        expires_at = int(time.time()) + int(expires_in) if expires_in else None
        self.db.upsert_user(
            id=user_id,
            ms_user_id=user.get("ms_user_id"),
            email=user.get("email"),
            display_name=user.get("display_name"),
            access_token=access,
            refresh_token=new_refresh,
            expires_at=expires_at
        )
        log(f"Refreshed token for {user_id}")
        return access

# ---------------------------
# OneDrive helpers
# ---------------------------
def graph_get(access_token: str, url: str, params=None):
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

def fetch_children_recursive(access_token: str, item_id="root"):
    """
    Fetch all items under the drive recursively using children listing.
    This is not delta API; it's stable, simple, and works for full scans.
    """
    items = []
    # initial children
    url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/children"
    while True:
        r = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        data = r.json()
        for it in data.get("value", []):
            items.append(it)
            if "folder" in it:
                # recursively fetch children of this folder
                items.extend(fetch_children_recursive(access_token, it["id"]))
        # no pagination for this simple approach; break
        break
    return items

def get_local_path_from_item(base_dir: str, item: dict):
    # Uses parentReference.path to reconstruct full path
    parent_ref = item.get("parentReference", {})
    path = parent_ref.get("path", "")
    # remove leading /drive/root:
    if path.startswith("/drive/root:"):
        sub = path.replace("/drive/root:", "").strip("/")
    else:
        sub = path.strip("/")
    if sub:
        return os.path.join(base_dir, sub, item.get("name"))
    else:
        return os.path.join(base_dir, item.get("name"))

def ensure_folder(path: str):
    os.makedirs(path, exist_ok=True)

def download_graph_file(access_token: str, item: dict, local_path: str):
    if "file" not in item:
        return
    dl = item.get("@microsoft.graph.downloadUrl")
    if not dl:
        # try fetch item metadata to get downloadUrl
        info = graph_get(access_token, f"https://graph.microsoft.com/v1.0/me/drive/items/{item['id']}")
        dl = info.get("@microsoft.graph.downloadUrl")
        if not dl:
            log(f"No download url for {item['id']}")
            return
    # stream write
    r = requests.get(dl, stream=True)
    r.raise_for_status()
    tmp = local_path + ".partial"
    ensure_folder(os.path.dirname(local_path))
    with open(tmp, "wb") as f:
        for chunk in r.iter_content(1024 * 64):
            if chunk:
                f.write(chunk)
    os.replace(tmp, local_path)
    log(f"Downloaded {local_path}")

# ---------------------------
# Sync core
# ---------------------------
class OneDriveSyncCore:
    def __init__(self, db: SyncDB, auth: AuthManager):
        self.db = db
        self.auth = auth

    def sync_user(self, user_id: str, display_name: str):
        """
        Main sync entry point.
        - ensures base dir exists
        - loads metadata.json if present
        - decides full vs incremental (full if base missing or metadata missing)
        """
        user = self.db.get_user(user_id)
        if not user:
            log(f"No DB entry for user {user_id}. Authenticate first with --authenticate")
            return

        # ensure valid token
        access_token = user.get("access_token")
        expires_at = user.get("expires_at") or 0
        if not access_token or int(time.time()) > int(expires_at) - 60:
            access_token = self.auth.refresh_access_token(user_id)
            if not access_token:
                log("Cannot obtain access token for sync")
                return

        base_dir = os.path.join(BASE_ROOT, display_name , "onedrive")
        metadata_path = os.path.join(base_dir, "metadata.json")

        # If base_dir or metadata missing => do full scan+download
        if (not os.path.exists(base_dir)) or (not os.path.exists(metadata_path)):
            log("Base dir or metadata missing -> performing full sync")
            ensure_folder(base_dir)
            self._full_sync(access_token, user_id, base_dir, metadata_path)
            return

        # otherwise do incremental sync (in this simple version we still list all files and compare to DB)
        log("Starting incremental sync")
        # mark existing items as not seen
        self.db.mark_all_not_seen(user_id)

        # fetch all items (recursive)
        all_items = fetch_children_recursive(access_token, "root")
        # process
        to_download = []
        for it in all_items:
            mapped = {
                "id": it["id"],
                "name": it.get("name"),
                "folder": "folder" in it,
                "size": it.get("size"),
                "parent_id": (it.get("parentReference") or {}).get("id"),
                "microsoft_path": (it.get("parentReference") or {}).get("path", "") + "/" + it.get("name"),
                "local_path": get_local_path_from_item(base_dir, it),
                "created_at_utc": it.get("createdDateTime"),
                "created_by": (it.get("createdBy") or {}).get("user", {}).get("email"),
                "modified_at_utc": it.get("lastModifiedDateTime"),
                "modified_by": (it.get("lastModifiedBy") or {}).get("user", {}).get("email"),
                "etag": it.get("eTag")
            }
            # upsert into DB
            self.db.upsert_drive_item(mapped, user_id, local_path=mapped["local_path"])
            # decide if to download
            if not mapped["folder"]:
                # check if file exists and etag matches
                cur = self.db.conn.execute("SELECT etag, local_path FROM drive_items WHERE id = ?", (mapped["id"],)).fetchone()
                do_download = True
                if cur:
                    stored_etag = cur["etag"]
                    local_path = cur["local_path"]
                    if stored_etag == mapped["etag"] and local_path and os.path.exists(local_path):
                        do_download = False
                if do_download:
                    to_download.append((it, mapped["local_path"]))

        # delete missing items locally (those last_seen==0)
        ids, local_paths = self.db.delete_items_not_seen(user_id)
        for p in local_paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                    log(f"Removed local file due to remote deletion: {p}")
                except Exception as e:
                    log(f"Failed to remove {p}: {e}")

        # download newly changed files
        for it, local_path in to_download:
            try:
                download_graph_file(access_token, it, local_path)
            except Exception as e:
                log(f"Download failed for {it.get('id')}: {e}")

        # refresh metadata.json from DB for human inspection
        self._write_metadata_json_from_db(user_id, base_dir, metadata_path)
        log("Incremental sync complete")

    def _full_sync(self, access_token: str, user_id: str, base_dir: str, metadata_path: str):
        # Full recursive scan and download everything (simple approach)
        log("Performing full recursive fetch")
        all_items = fetch_children_recursive(access_token, "root")

        # mark all as not seen first
        self.db.mark_all_not_seen(user_id)

        to_download = []
        for it in all_items:
            mapped = {
                "id": it["id"],
                "name": it.get("name"),
                "folder": "folder" in it,
                "size": it.get("size"),
                "parent_id": (it.get("parentReference") or {}).get("id"),
                "microsoft_path": (it.get("parentReference") or {}).get("path", "") + "/" + it.get("name"),
                "local_path": get_local_path_from_item(base_dir, it),
                "created_at_utc": it.get("createdDateTime"),
                "created_by": (it.get("createdBy") or {}).get("user", {}).get("email"),
                "modified_at_utc": it.get("lastModifiedDateTime"),
                "modified_by": (it.get("lastModifiedBy") or {}).get("user", {}).get("email"),
                "etag": it.get("eTag")
            }
            self.db.upsert_drive_item(mapped, user_id, local_path=mapped["local_path"])
            if not mapped["folder"]:
                to_download.append((it, mapped["local_path"]))

        # delete items not seen (cleanup)
        ids, local_paths = self.db.delete_items_not_seen(user_id)
        for p in local_paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

        # download everything
        for it, local_path in to_download:
            try:
                download_graph_file(access_token, it, local_path)
            except Exception as e:
                log(f"Full-download failed for {it.get('id')}: {e}")

        # write metadata.json
        self._write_metadata_json_from_db(user_id, base_dir, metadata_path)
        log("Full sync complete")

    def _write_metadata_json_from_db(self, user_id: str, base_dir: str, metadata_path: str):
        cur = self.db.conn.cursor()
        cur.execute("SELECT id, name, local_path, modified_at_utc FROM drive_items WHERE user_id = ?", (user_id,))
        rows = cur.fetchall()
        meta = {}
        for r in rows:
            meta[r["id"]] = {
                "name": r["name"],
                "path": r["local_path"],
                "lastModifiedDateTime": r["modified_at_utc"]
            }
        ensure_folder(os.path.dirname(metadata_path))
        with open(metadata_path, "w") as f:
            json.dump(meta, f, indent=2)



# ---------------------------
# CLI
# ---------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--authenticate", "--auth", action="store_true", help="Run device code auth and store tokens")
    parser.add_argument("--sync", type=str, help="Run sync for given user id (email)")
    args = parser.parse_args()

    # Ensure DB dir exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = SyncDB(DB_PATH)
    auth = AuthManager(db)
    core = OneDriveSyncCore(db, auth)

    if args.authenticate:
        user_id, display_name = auth.device_code_flow()
        log(f"Authenticated {display_name} ({user_id})")
        return

    if args.sync:
        user_id = args.sync

        user = db.get_user(user_id)
        if not user:
            raise RuntimeError(f"User not found in database: {user_id}")

        display_name = user.get("display_name") or user.get("name") or user_id

        log(f"Starting sync for {display_name} ({user_id})")
        core.sync_user(user_id, display_name)
        return

    parser.print_help()

if __name__ == "__main__":
    main()
