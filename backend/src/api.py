from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from urllib.parse import urlencode
import requests
import time

from src.sync_db import SyncDB
from src.sync_service import OneDriveSyncCore, AuthManager, graph_get

# ================= CONFIG =================

DB_PATH = "/project/backend/db/sync.db"

# FRONTEND callback (Microsoft redirects HERE)
FRONTEND_ORIGIN = "http://localhost:3000"
REDIRECT_URI = f"{FRONTEND_ORIGIN}/auth/callback"

# ==========================================

db = SyncDB(DB_PATH)
auth_manager = AuthManager(db)
sync_core = OneDriveSyncCore(db, auth_manager)

app = FastAPI()

# ================= CORS ===================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================= BASIC ==================

@app.get("/")
def root():
    return {"status": "backend alive"}

# ================= USERS ==================

@app.get("/users")
def list_users():
    return db.list_users()

# ================= AUTH ===================

@app.get("/auth/mslogin/url")
def get_ms_login_url():
    params = {
        "client_id": auth_manager.CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "response_mode": "query",
        "scope": " ".join(auth_manager.SCOPES),
    }

    url = (
        f"https://login.microsoftonline.com/"
        f"{auth_manager.TENANT_ID}/oauth2/v2.0/authorize?"
        f"{urlencode(params)}"
    )
    return {"url": url}


# 🔑 BACKEND-ONLY endpoint: frontend calls this
@app.get("/auth/exchange")
def exchange_code(code: str, background_tasks: BackgroundTasks):
    try:
        token_url = (
            f"https://login.microsoftonline.com/"
            f"{auth_manager.TENANT_ID}/oauth2/v2.0/token"
        )

        data = {
            "client_id": auth_manager.CLIENT_ID,
            "client_secret": auth_manager.CLIENT_SECRET,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(auth_manager.SCOPES),
        }

        r = requests.post(token_url, data=data, timeout=10)
        r.raise_for_status()
        tokens = r.json()

        # Fetch user info
        user_info = graph_get(
            tokens["access_token"],
            "https://graph.microsoft.com/v1.0/me"
        )

        email = user_info["userPrincipalName"]
        display_name = user_info.get("displayName") or email

        db.upsert_user(
            id=email,
            ms_user_id=user_info["id"],
            email=email,
            display_name=display_name,
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token"),
            expires_at=int(time.time()) + int(tokens["expires_in"]),
        )

        background_tasks.add_task(sync_core.sync_user, email)

        return {
            "message": "User authorized and sync started",
            "email": email,
            "display_name": display_name,
        }

    except requests.RequestException as e:
        return JSONResponse(
            {"error": "Token exchange failed", "details": str(e)},
            status_code=400,
        )

# ================= SYNC ===================

@app.get("/sync/status/{user_id}")
def get_sync_status(user_id: str):
    status = db.get_sync_status(user_id)
    return status or {"state": "unknown"}


@app.post("/sync/start/{user_id}")
def start_sync(user_id: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(sync_core.sync_user, user_id)
    return {"state": "started"}
