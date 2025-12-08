#!/usr/bin/env python3
"""
sync_service.py

Cleaner, single-file OneDrive sync service that uses:
- sync_db.SyncDB (SQLite layer file: sync_db.py)
- requests, msal
- Stores files under /opt/thalamind/{user_id}/onedrive

Usage:
  python3 sync_service.py --authenticate   # do device-code authentication for a user (one-time)
  python3 sync_service.py --sync <user_id> # run one sync pass for a user (incremental if delta exists)

Designed for being invoked from systemd (see unit example below).
"""

import os
import json
import time
import argparse
import requests
from typing import Optional, Tuple, Dict, Any, List

# Local DB layer - assumed present from previous work
from sync_db import SyncDB

# CONFIG: paths and credentials file
DB_PATH = "/opt/thalamind/db/sync.db"
CREDENTIALS_FILE = "app_credentials.json"  # same as you already use
BASE_STORAGE = "/opt/thalamind"             # per-user root

# Load app credentials
with open(CREDENTIALS_FILE, "r") as f:
    CRED = json.load(f)

CLIENT_ID = CRED["CLIENT_ID"]
TENANT_ID = CRED["TENANT_ID"]
SCOPES = CRED["SCOPES"]  # list of scopes
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
TOKEN_ENDPOINT = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

# Simple logger
def log(msg: str):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


class AuthManager:
    """
    Manage device-code flow for initial auth + refresh token usage.
    Stores tokens to SyncDB.users via db.upsert_user (access_token, refresh_token).
    """
    def __init__(self, db: SyncDB):
        self.db = db

    def device_code_auth(self) -> Dict[str, Any]:
        """
        Perform device code flow using MSAL public client (blocking).
        Returns token_result dict (access_token, refresh_token, id_token_claims, etc.)
        """
        import msal
        app = msal.PublicClientApplication(client_id=CLIENT_ID, authority=AUTHORITY)
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "message" in flow:
            log(flow["message"])
        else:
            raise RuntimeError("Failed to initiate device flow")

        result = app.acquire_token_by_device_flow(flow)  # blocks until complete or error
        if "access_token" not in result:
            raise RuntimeError(f"Authentication failed: {result}")
        return result

    def persist_tokens_for_user(self, user_local_id: str, ms_user_id: str, email: str, display_name: str, token_result: Dict[str, Any]):
        """
        Store tokens and expiry in DB. Use db.upsert_user.
        Note: tokens are stored as plaintext here for demo. Consider encrypting/using keyring.
        """
        access = token_result.get("access_token")
        refresh = token_result.get("refresh_token")
        expires_in = token_result.get("expires_in")
        expires_at = int(time.time()) + int(expires_in) if expires_in else None

        self.db.upsert_user(
            id=user_local_id,
            ms_user_id=ms_user_id,
            email=email,
            display_name=display_name,
            access_token=access,
            refresh_token=refresh,
            expires_at=expires_at
        )
        log(f"Persisted tokens for local user {user_local_id}")

    def refresh_access_token(self, user_id: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
        """
        Try to refresh access token using refresh_token grant.
        Returns tuple (access_token, refresh_token, expires_at) or (None, None, None) on failure.
        """
        user_row = self.db.get_user(user_id)
        if not user_row:
            log(f"User {user_id} not found in DB for refresh")
            return None, None, None

        refresh_token = user_row.get("refresh_token")
        if not refresh_token:
            log("No refresh token available")
            return None, None, None

        payload = {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            # public client apps typically don't have client_secret
            "scope": " ".join(SCOPES)
        }
        r = requests.post(TOKEN_ENDPOINT, data=payload)
        if r.status_code != 200:
            log(f"Refresh token request failed: {r.status_code} {r.text}")
            return None, None, None
        jr = r.json()
        access = jr.get("access_token")
        new_refresh = jr.get("refresh_token", refresh_token)  # sometimes refreshed
        expires_in = jr.get("expires_in")
        expires_at = int(time.time()) + int(expires_in) if expires_in else None

        # Persist updated tokens
        self.db.upsert_user(
            id=user_id,
            ms_user_id=user_row.get("ms_user_id"),
            email=user_row.get("email"),
            display_name=user_row.get("display_name"),
            access_token=access,
            refresh_token=new_refresh,
            expires_at=expires_at
        )
        log(f"Refreshed token for {user_id}, expires_at={expires_at}")
        return access, new_refresh, expires_at


class OneDriveClient:
    """
    Minimal Graph client for OneDrive operations we need.
    """
    BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, access_token: str):
        self.access_token = access_token
        self.headers = {"Authorization": f"Bearer {self.access_token}"}

    def _get(self, url: str, params: dict = None) -> dict:
        r = requests.get(url, headers=self.headers, params=params)
        r.raise_for_status()
        return r.json()

    def get_drive_delta(self, delta_link: Optional[str] = None) -> Tuple[List[dict], Optional[str]]:
        """
        If delta_link is provided, call it directly.
        Otherwise call /me/drive/root/delta to do initial delta (works as full listing + delta).
        Returns (items_list, new_delta_link)
        """
        url = delta_link if delta_link else f"{self.BASE}/me/drive/root/delta"
        items: List[dict] = []
        next_url = url

        while next_url:
            r = requests.get(next_url, headers=self.headers)
            r.raise_for_status()
            data = r.json()
            items.extend(data.get("value", []))

            # nextLink / deltaLink handling
            if "@odata.nextLink" in data:
                next_url = data["@odata.nextLink"]
            elif "@odata.deltaLink" in data:
                return items, data["@odata.deltaLink"]
            else:
                # malformed or last page without deltaLink
                break

        # If we reach here without deltaLink (rare), return items and None
        return items, None

    def get_item(self, item_id: str) -> dict:
        url = f"{self.BASE}/me/drive/items/{item_id}"
        return self._get(url)

    def download_via_url(self, download_url: str) -> bytes:
        # downloadUrl is a pre-authenticated URL; we can use requests.get directly
        r = requests.get(download_url, stream=True)
        r.raise_for_status()
        return r.content


class OneDriveSyncService:
    def __init__(self, db: SyncDB, auth: AuthManager):
        self.db = db
        self.auth = auth

    # Utilities mapping between Graph item and our DB item shape
    def _map_graph_item(self, graph_item: dict, user_id: str, local_base: str) -> dict:
        """
        Convert a Graph item JSON to the internal dict used by SyncDB.upsert_drive_item.
        We store parentReference json string so we can reconstruct local path later.
        """
        parent_ref = graph_item.get("parentReference") or {}
        microsoft_path = parent_ref.get("path", "") + "/" + graph_item.get("name", "")
        local_path = self._get_local_path_from_parent_ref(parent_ref, graph_item.get("name"), local_base)

        return {
            "id": graph_item["id"],
            "name": graph_item.get("name"),
            "folder": "folder" in graph_item,
            "size": graph_item.get("size"),
            "parent_id": parent_ref.get("id"),
            "microsoft_path": microsoft_path,
            "local_path": local_path,
            "created_at_utc": graph_item.get("createdDateTime"),
            "created_by": (graph_item.get("createdBy") or {}).get("user", {}).get("email"),
            "modified_at_utc": graph_item.get("lastModifiedDateTime"),
            "modified_by": (graph_item.get("lastModifiedBy") or {}).get("user", {}).get("email"),
            "etag": graph_item.get("eTag")
        }

    def _get_local_path_from_parent_ref(self, parent_ref: dict, name: str, local_base: str) -> str:
        """
        Reconstruct the local path from parentReference.path which looks like:
         "/drive/root:/Documents/Work"
        We convert it into: {local_base}/Documents/Work/{name}
        """
        pr_path = parent_ref.get("path", "")
        # default to root
        if pr_path.startswith("/drive/root:"):
            sub = pr_path.replace("/drive/root:", "").strip("/")
        else:
            sub = pr_path.strip("/")
        return os.path.join(local_base, sub, name) if sub else os.path.join(local_base, name)

    def _ensure_local_folder(self, local_path: str):
        folder = os.path.dirname(local_path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder, exist_ok=True)

    def _download_item(self, client: OneDriveClient, graph_item: dict, local_path: str):
        if "file" not in graph_item:
            # nothing to download
            return
        download_url = graph_item.get("@microsoft.graph.downloadUrl")
        if not download_url:
            # fallback: fetch item metadata to obtain downloadUrl
            meta = client.get_item(graph_item["id"])
            download_url = meta.get("@microsoft.graph.downloadUrl")
            if not download_url:
                log(f"No download URL for item {graph_item['id']}")
                return
        # ensure folder exists
        self._ensure_local_folder(local_path)
        content = client.download_via_url(download_url)
        with open(local_path, "wb") as f:
            f.write(content)
        log(f"Downloaded file to {local_path}")

    def run_incremental_sync(self, user_id: str):
        """
        Attempt an incremental sync using stored delta token.
        If no delta token is present, falls back to a full scan (which uses delta initial endpoint).
        """
        user = self.db.get_user(user_id)
        if not user:
            raise RuntimeError(f"User {user_id} not found in DB")

        # Ensure tokens are fresh
        access_token = user.get("access_token")
        expires_at = user.get("expires_at") or 0
        if not access_token or int(time.time()) > int(expires_at) - 60:
            # refresh
            access_token, _, _ = self.auth.refresh_access_token(user_id)
            if not access_token:
                raise RuntimeError("Unable to obtain access token for user")

        client = OneDriveClient(access_token)
        sync_state = self.db.get_sync_state(user_id)
        delta_link = sync_state.get("onedrive_delta")
        log(f"Starting incremental sync for {user_id}; delta exists? {'yes' if delta_link else 'no'}")

        # mark all items as not seen so we can detect deletions
        self.db.mark_all_not_seen(user_id)

        items, new_delta = client.get_drive_delta(delta_link)

        log(f"Delta returned {len(items)} items; new_delta present? {'yes' if new_delta else 'no'}")

        # Process items
        to_download = []
        local_base = os.path.join(BASE_STORAGE, user_id, "onedrive")
        for gi in items:
            mapped = self._map_graph_item(gi, user_id, local_base)
            # upsert into DB (SyncDB.upsert_drive_item expects item dict and user_id)
            self.db.upsert_drive_item(mapped, user_id, local_path=mapped["local_path"])
            # If file: decide whether to download. Use etag/modified detection.
            if not mapped["folder"]:
                # fetch stored row to compare etag (we can query DB for current row)
                # For simplicity, we will always download if the file doesn't exist locally or if etag changed.
                current = self.db.conn.execute("SELECT etag, local_path FROM drive_items WHERE id = ?", (mapped["id"],)).fetchone()
                local_path = mapped["local_path"]
                do_download = True
                if current:
                    stored_etag = current["etag"]
                    if stored_etag == mapped["etag"] and os.path.exists(local_path):
                        do_download = False
                if do_download:
                    to_download.append( (gi, local_path) )

        # Delete items not seen (db.delete_items_not_seen returns ids and paths)
        ids, local_paths = self.db.delete_items_not_seen(user_id)
        for p in local_paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                    log(f"Removed local file due to deletion: {p}")
                except Exception as e:
                    log(f"Failed to remove {p}: {e}")

        # Download needed files
        for gi, local_path in to_download:
            try:
                self._download_item(client, gi, local_path)
            except Exception as e:
                log(f"Failed to download {gi.get('id')}: {e}")

        # Persist new delta
        if new_delta:
            self.db.set_sync_state(user_id, onedrive_delta=new_delta, last_incremental_sync=int(time.time()))
        else:
            # If no delta returned, still update last_incremental_sync; next run will perform initial again
            self.db.set_sync_state(user_id, last_incremental_sync=int(time.time()))

        log("Incremental sync complete")

    def run_full_sync(self, user_id: str):
        """
        Convenience wrapper that currently calls run_incremental_sync (because
        Graph's initial delta acts like a full listing).
        Kept separate for future different full-scan logic.
        """
        self.run_incremental_sync(user_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OneDrive Sync Service (simple)")
    parser.add_argument("--authenticate", action="store_true", help="Run device-code auth and persist tokens for a user")
    parser.add_argument("--sync", nargs=1, help="Run a sync for given local user id (e.g. alice-local-1)")

    args = parser.parse_args()

    # Ensure DB dir exists and instantiate DB
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    db = SyncDB(DB_PATH)
    auth = AuthManager(db)
    service = OneDriveSyncService(db, auth)

    if args.authenticate:
        # Do interactive device-flow to get tokens and persist a DB user row.
        token_res = auth.device_code_auth()
        # id token claims usually include preferred_username or email and oid
        claims = token_res.get("id_token_claims", {})
        ms_user_id = claims.get("oid") or claims.get("sub") or "ms-user-unknown"
        email = claims.get("preferred_username") or claims.get("email") or f"{ms_user_id}@example.com"
        display_name = claims.get("name") or email

        # Build a local user id (you might want to map this to better-auth user.id)
        local_user_id = email  # choose how you want to map - for demo we use email as local id
        auth.persist_tokens_for_user(local_user_id, ms_user_id, email, display_name, token_res)
        log(f"Authenticated and stored user {local_user_id}")

    elif args.sync:
        local_user_id = args.sync[0]
        try:
            service.run_incremental_sync(local_user_id)
        except Exception as e:
            log(f"Sync failed: {e}")
    else:
        parser.print_help()
