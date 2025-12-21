Thalamind OneDrive Sync Service
================================

Overview
--------
This repository implements a small OneDrive sync service used to keep a local folder tree in sync with a user's Microsoft OneDrive contents. It authenticates users via Microsoft's device-code flow, stores short-lived tokens in a small SQLite database, downloads files to a local path, and removes local files when corresponding items are deleted or moved in OneDrive.

This README documents how the system works, how to run it, the structure of the database, and the contents of the `app_credentials.json` credential file.

Quick checklist
---------------
- [ ] Read the architecture and components below
- [ ] Configure `backend/app_credentials.json` (the repository already contains sample values)
- [ ] Authenticate a user: `python3 backend/src/sync_service.py --authenticate`
- [ ] Run a sync for an authenticated user: `python3 backend/src/sync_service.py --sync user@example.com`
- [ ] (Optional) Install and enable the systemd unit templates under `backend/systemd/` for automatic periodic sync

Architecture & components
-------------------------
- backend/src/sync_service.py — main service and CLI. Implements authentication (MSAL/device-code), listing OneDrive items using Microsoft Graph, downloading files, and the sync logic (full/incremental).
- backend/src/sync_db.py — a thin SQLite wrapper (SyncDB) that stores users and drive_items and provides convenience methods used by the service.
- backend/app_credentials.json — application credentials used to call Microsoft OAuth/Graph APIs (CLIENT_ID, CLIENT_SECRET, TENANT_ID and SCOPES).
- backend/db/sync.db — SQLite database file where users and drive_items are persisted (created automatically the first time the service runs).
- backend/systemd/ — systemd unit and timer templates (onedrive-sync@.service, onedrive-sync@.timer) for running sync periodically per-user.

How the system works (high level)
---------------------------------
1. Authenticate a user via device-code flow. The service opens a device-code prompt with instructions; when the user completes authentication in their browser a short-lived access token and refresh token are stored in the local SQLite database.
2. When a sync runs for a user, the service lists the user's OneDrive recursively using Microsoft Graph. For each item it:
   - calculates a local path based on the item's OneDrive path and a configured base directory;
   - upserts the item into the `drive_items` table (used to detect moves/renames/deletions and avoid re-downloading unchanged files);
   - downloads file contents to the local path when the item is a file;
   - after the listing, any items previously stored in the DB but not seen in the current listing are deleted from the DB and the local filesystem.

Key runtime constants (in `sync_service.py`)
-------------------------------------------
- PROJECT_ROOT — repository root inferred from the `src` folder.
- CREDENTIALS_FILE — points to `backend/app_credentials.json` relative to PROJECT_ROOT.
- DB_PATH — currently set in the script to `/project/backend/db/sync.db`. Note: this is an absolute path used by the code; see "Paths & deployment" below for implications.
- BASE_ROOT — root base directory where user folders are created (default in code is `/opt/thalamind`). Under this directory the service creates a folder per user (by display name) and a `onedrive` subfolder where files are stored.

Database schema
---------------
The service uses a single SQLite database with two tables: `users` and `drive_items`.

users table
- id (TEXT PRIMARY KEY) — unique user id used by the service (the code uses the authenticated user's email as the `id`).
- ms_user_id (TEXT) — the Microsoft user object id (OID).
- email (TEXT) — user email address.
- display_name (TEXT) — user display name.
- access_token (TEXT) — current access token (short lived).
- refresh_token (TEXT) — refresh token used to obtain new access tokens.
- expires_at (INTEGER) — unix timestamp when the access token expires.

drive_items table
- id (TEXT PRIMARY KEY) — Microsoft drive item id.
- user_id (TEXT) — id of the user that owns this item (references users.id semantically).
- name (TEXT) — file/folder name.
- folder (INTEGER) — 1 if this item is a folder, 0 if a file.
- size (INTEGER) — item size in bytes (for files).
- parent_id (TEXT) — parent item id in OneDrive.
- microsoft_path (TEXT) — the OneDrive path (parentReference.path) used to compute local path.
- local_path (TEXT) — computed path on the local filesystem where the file is stored.
- etag (TEXT) — ETag from Graph; useful for change detection (the current implementation upserts by id and replaces fields).
- created_at_utc (TEXT) — createdDateTime from Graph.
- modified_at_utc (TEXT) — lastModifiedDateTime from Graph.
- last_seen (INTEGER) — unix timestamp indicating when the item was last present in a listing (0 used for items not seen during the most recent listing; used to delete items no longer present in OneDrive).

Authentication and credentials (`backend/app_credentials.json`)
-------------------------------------------------------------
The `app_credentials.json` file holds the application credentials used to request tokens and call Microsoft Graph. Fields:
- CLIENT_ID — The application (client) id assigned by Azure AD for your registered app.
- CLIENT_SECRET — Client secret (if your app requires a confidential client). Note: the current code uses MSAL's PublicClientApplication and the device-code flow which does not use CLIENT_SECRET, but the project includes the secret field and it must be kept secure. If you run confidential-client flows you'll need this secret.
- TENANT_ID — Tenant id (guid) for your Azure AD tenant. Used to build the authority URL (https://login.microsoftonline.com/{TENANT_ID}).
- SCOPES — array of Graph scopes the app will request. Example in the repository: ["Files.ReadWrite.All", "User.Read"].

Important notes about auth flow used by the code
- The code uses MSAL device-code flow for interactive authentication (`AuthManager.device_code_flow`). This is ideal for headless machines because the user only needs to visit a URL and enter the code shown.
- After the device flow completes, the service stores `access_token`, `refresh_token` and `expires_at` in the `users` table.
- When running a sync the service will refresh the token automatically if it is close to expiry using a direct POST to the token endpoint and the saved refresh token.

How to run (local development)
------------------------------
1. Ensure requirements are installed. The service uses the following Python dependencies:
   - requests
   - msal
   Install them (recommended inside a virtualenv):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt || pip install requests msal
```

If there's no `backend/requirements.txt` add one or use the pip command above.

2. Check `backend/app_credentials.json` contains valid values for your Azure AD application.

3. Authenticate a user (device code flow):

```bash
python3 backend/src/sync_service.py --authenticate
```

Follow the printed instructions to authenticate in a web browser. After successful authentication the user's tokens will be stored in the SQLite database.

4. Run a sync for that user (use the email shown after authentication):

```bash
python3 backend/src/sync_service.py --sync user@example.com
```

This will perform a full sync the first time and will download files to the local path computed from `BASE_ROOT` and the user's `display_name`.

Paths & deployment notes
------------------------
- DB_PATH in `sync_service.py` is set to `/project/backend/db/sync.db`. The code creates the directory if it doesn't exist. In many deployments you will want to change this to an absolute path where the service has write permission (for example `/var/lib/thalamind/sync.db` or keep it under the project folder).
- BASE_ROOT defaults to `/opt/thalamind`. The service creates `BASE_ROOT/{display_name}/onedrive` and stores synced files there. Make sure the user running the service has write permissions.
- The systemd unit templates in `backend/systemd/` are prepared to run the service periodically per-user. The service binary in the template should point to the `sync_service.py` path and use the `--sync` flag with the correct user id.

Security
--------
- Treat `CLIENT_SECRET` and the `refresh_token` values in the SQLite database as secrets. Do not check them into source control or expose them publicly.
- Use appropriate file permissions on `backend/app_credentials.json` (e.g., `chmod 600`) and on the database file.
- Consider using a secrets store or environment variables in production rather than a plain JSON file for storing client secrets.

Troubleshooting
---------------
- "invalid_grant" during refresh: the stored refresh token may have been revoked or expired. Re-run `--authenticate` for the user to generate a fresh token.
- 401/403 errors from Graph: ensure the app is granted the scopes in Azure AD and consented properly. For delegated permissions (device-code) the user must consent or an admin must pre-consent.
- Files not downloading: ensure the `BASE_ROOT` directory exists and the process has write permission to the user's folder.
- DB not created: confirm the process can create the directory for `DB_PATH` (parent dir permissions).

Development tips
----------------
- The code is intentionally small and synchronous. If you need higher throughput or many concurrent users consider converting the download/listing logic to asynchronous I/O or a queue-based architecture.
- Consider adding ETag-based checks to avoid re-downloading unchanged files (store ETag and compare before downloading).
- Add unit tests around the DB wrapper (`backend/src/sync_db.py`) and mock Graph API responses for the sync logic.

Files of interest
-----------------
- backend/src/sync_service.py — main CLI, auth, and sync logic
- backend/src/sync_db.py — lightweight SQLite wrapper and schema
- backend/app_credentials.json — credentials used by the service
- backend/systemd/ — systemd unit and timer templates

Contact / Next steps
--------------------
If you want, I can also:
- Convert `DB_PATH` and `BASE_ROOT` into configurable environment variables or CLI flags.
- Add a small `requirements.txt` and a sample systemd service file with working paths for a packaged deployment.
- Implement ETag checks to avoid unnecessary downloads.



