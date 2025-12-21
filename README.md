# Thalamind OneDrive Sync (Backend + UI)

This repository contains a small OneDrive synchronization service and a Next.js frontend skeleton. The system is a backend service that uses Microsoft Graph to synchronize a user's OneDrive files into a local folder and keeps a small SQLite database with sync metadata.

This project is not fully finished — the README documents the current design, how to run it for local development, and the important configuration pieces (including OAuth app credentials and required permissions). Do not commit real client secrets to source control; the repo includes an `app_credentials.json` file but you should replace values with your own app registration and keep secrets private.

---

Checklist (what I'll cover):
- What the system does and high-level architecture
- How the OAuth flow works and where credentials are used
- Database schema and where data is stored
- How to run locally (backend + sync script) and how systemd units/timers are used
- Known issues and troubleshooting tips

---

Project layout (important files/folders)
- backend/
  - app_credentials.json    # OAuth app config (DO NOT commit real secrets)
  - requirements.txt       # Python dependencies for backend
  - run_continous_sync.sh  # helper to continuously run a single-user sync
  - db/sync.db             # SQLite DB (created automatically)
  - src/
    - api.py               # FastAPI backend (HTTP API used by the frontend)
    - sync_service.py      # Core sync logic + CLI entrypoint
    - sync_db.py           # SQLite helper and schema
    - models.py            # Pydantic response models
- ui/                      # Next.js frontend (minimal)

High-level description
----------------------
The system provides a backend service that:
1. Lets a user sign in with Microsoft (OAuth) using the frontend.
2. Exchanges the authorization code for tokens in the backend and saves tokens to the local SQLite DB.
3. Starts a synchronization job that enumerates files in the user's OneDrive (via Microsoft Graph), downloads files to a local folder, and keeps metadata in the DB.
4. Exposes endpoints to check sync status and to list users.

Components
- FastAPI backend (`backend/src/api.py`): exposes endpoints used by the frontend and for triggering sync operations.
- Sync core (`backend/src/sync_service.py`): handles listing OneDrive items, downloading files, handling token refresh, and driving DB updates.
- Database (`backend/db/sync.db`): SQLite database with three main tables (`users`, `drive_items`, `sync_status`) handled by `SyncDB`.
- Frontend: Next.js app that provides a UI and a redirect/callback for Microsoft OAuth.

OAuth / App credentials
------------------------
The backend reads `backend/app_credentials.json`. DO NOT store real secrets in public repositories. Use environment-specific secrets (vault, env variables, or other secure storage) in production.

Format (replace values with your own):

{
  "CLIENT_ID": "<your-client-id>",
  "CLIENT_SECRET": "<your-client-secret>",
  "TENANT_ID": "<your-tenant-id>",
  "SCOPES": ["Files.ReadWrite.All", "User.Read", "offline_access"]
}

Required permissions / consent
- Files.ReadWrite.All (or Files.Read.All for read-only): access to OneDrive files.
- User.Read: read basic profile information (used to identify the user/email).
- offline_access: to obtain refresh tokens for long-lived background sync.

Notes on not exposing secrets: the included `app_credentials.json` file in the repo contains placeholder or example values — replace them locally. Never paste client secrets into public issues or public git commits.

How the OAuth flow works (simplified)
1. Frontend requests the login URL from the backend at `/auth/mslogin/url`.
2. Frontend redirects user to Microsoft login (authorization endpoint) with the app's client id, requested scopes, and redirect URI.
3. After user consents, Microsoft redirects back to the frontend redirect URI with a `code` parameter.
4. Frontend calls the backend `/auth/exchange?code=...` endpoint (backend-only endpoint) which posts the `code` to the token endpoint and receives access/refresh tokens.
5. Backend fetches the user's profile from Microsoft Graph and stores tokens and metadata in the `users` table. A background sync task is started for that user.

Database (SQLite) — schema overview
----------------------------------
The DB file is expected at: `/project/backend/db/sync.db` in the current code. If you're running locally from the repository root, you may prefer to change the DB path in the code to `./backend/db/sync.db` or create a `/project` symlink pointing to your repo root.

Main tables (implemented in `backend/src/sync_db.py`):
- users
  - id (primary key): internal user id (email is used in code)
  - ms_user_id: Microsoft user id (used to name local storage folder)
  - email, display_name
  - access_token, refresh_token, expires_at (unix timestamp)

- drive_items
  - (id, user_id) composite primary key
  - name, folder (bool), size, parent_id
  - microsoft_path: path reported by Graph
  - local_path: local filesystem path where the file is stored
  - etag, created_at_utc, modified_at_utc, last_seen

- sync_status
  - user_id (primary key)
  - state: 'idle' | 'running' | 'error'
  - last_started, last_finished, last_error

The `SyncDB` helper contains methods to upsert users and drive items, mark items as not seen, delete items not seen after a listing (to handle deletions), and record the sync state.

Running locally (development)
-----------------------------
1. Create a Python virtual environment inside `backend` and install dependencies:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Fix the DB path or ensure the absolute path used in code exists.

The code currently uses absolute paths like `/project/backend/db/sync.db` and `/opt/thalamind` for local storage in `sync_service.py` and `api.py`. For local dev you can either:
- Create a `/project` directory and symlink your repo root there: sudo ln -s "$(pwd)" /project
- OR edit `backend/src/sync_service.py` and `backend/src/api.py` to use relative/local paths (recommended for dev).

3. Start the FastAPI backend (from repo root or `backend` directory):

```bash
cd backend
source .venv/bin/activate
uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
```

4. Visit the frontend (if you're running the Next.js UI) or call the endpoints directly:
- GET / -> health check
- GET /users -> list users
- GET /auth/mslogin/url -> get URL to redirect the user to Microsoft sign-in
- GET /auth/exchange?code=... -> exchange code for tokens (backend-only; in practice frontend will call this after receiving the code)

Running the sync script manually
--------------------------------
You can run the sync script for one user from the CLI. The script expects a user id (email) which must exist in the DB (i.e., the user has completed OAuth):

```bash
cd backend
source .venv/bin/activate
python src/sync_service.py --sync "alice@example.com"
```

run_continous_sync.sh
---------------------
There is a helper script `backend/run_continous_sync.sh` that will continuously run `sync_service.py --sync "$USER_ID"` in an infinite loop. Set the `USER_ID` env var before running. The script assumes `backend/.venv/bin/python` exists and the project root is mounted at `/project` — adjust if running locally.

Systemd service & timer (example)
---------------------------------
A `systemd` unit (example) is provided in `systemd/` (the repo includes a sample). The intended flow is:
- `onedrive-sync@.service` — one-shot service that runs the sync script for a given user
- `onedrive-sync@.timer` — timer to execute the service periodically

Important: update the `User`, `WorkingDirectory`, and `ExecStart` paths in the unit file to match your local installation (virtualenv python path, project path, etc.).

Example service pitfalls:
- The project currently contains absolute paths (e.g., `/opt/thalamind`) which you must adapt.
- The systemd unit should run as the Linux user that owns the target destination directories (so files get created with correct ownership).

Security and secrets handling
----------------------------
- Do not commit `CLIENT_SECRET` or refresh tokens to public repos.
- Use environment variables, OS keyrings, or a secret manager in production.

Known issues / Limitations (current status)
------------------------------------------
- Absolute paths: many paths in code are absolute and expect the project mounted at `/project` and local storage at `/opt/thalamind` — adjust for your environment.
- Error handling: there is basic handling for token expiration (HTTP 401), but more robust retry/backoff and rate-limit handling is desirable.
- Concurrency: the sync mechanism is simple and not optimized for many users running concurrently.
- Partial implementation: the frontend is minimal; some UI flows may be incomplete.

Troubleshooting
---------------
- If requests to Microsoft fail with 401, the refresh flow tries to refresh the token. Check the `refresh_token` stored in the DB.
- If files are not being downloaded, verify the `@microsoft.graph.downloadUrl` is present and reachable; network issues can cause failures.
- If DB cannot be created, ensure the directory exists and is writable by the process user.

Next steps / improvements
------------------------
- Make paths configurable via environment variables or configuration file.
- Add better unit and integration tests for the sync logic.
- Harden token refresh and add exponential backoff for transient HTTP errors.
- Add secure secret storage for OAuth client secret.
- Improve the frontend OAuth UX and show progress for ongoing syncs.

Contact / notes
---------------
If you want, I can:
- Make paths configurable and update the code to use a dev-friendly default.
- Add a simple script to initialize the DB and create a sample user record for local testing.
