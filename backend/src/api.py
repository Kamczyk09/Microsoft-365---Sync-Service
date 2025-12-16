from src.sync_db import SyncDB
from src.models import UserResponse
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from src.sync_service import OneDriveSyncCore, AuthManager
from urllib.parse import urlencode
from fastapi import Request
import requests
import time
from src.sync_service import graph_get
from src.sync_service import CLIENT_ID, TENANT_ID, SCOPES





DB_PATH = "/project/backend/db/sync.db"

db = SyncDB(DB_PATH)
auth_manager = AuthManager(db)
sync_core = OneDriveSyncCore(db, auth_manager)

app = FastAPI()

# CORS for frontend
origins = ["http://localhost:3000", "http://46.62.236.228:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Do NOT reuse the same connection object across threads
def get_db():
    return SyncDB(DB_PATH)


@app.get("/users")
def list_users():
    db_instance = get_db()
    return db_instance.list_users()


@app.get("/auth/mslogin/url")
def get_ms_login_url():
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        # "redirect_uri": "http://46.62.236.228:3000/auth/callback",
        "redirect_uri": "http://localhost:3000/auth/callback",# frontend URL
        "response_mode": "query",
        "scope": " ".join(SCOPES)
    }
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize?{urlencode(params)}"
    return {"url": url}



@app.get("/auth/callback")
def ms_login_callback(request: Request, code: str, background_tasks: BackgroundTasks):
    """
    Handles Microsoft redirect with authorization code, exchanges for tokens,
    adds user to DB, and starts sync.
    """
    token_url = f"https://login.microsoftonline.com/{auth_manager.TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": auth_manager.CLIENT_ID,
        "scope": " ".join(auth_manager.SCOPES),
        "code": code,
        "redirect_uri": "http://46.62.236.228:3000/auth/callback",
        "grant_type": "authorization_code",
    }
    r = requests.post(token_url, data=data)
    r.raise_for_status()
    tokens = r.json()

    # Get user info from Graph
    user_info = graph_get(tokens["access_token"], "https://graph.microsoft.com/v1.0/me")
    email = user_info["userPrincipalName"]
    display_name = user_info.get("displayName") or email

    # Upsert user in DB
    db.upsert_user(
        id=email,
        ms_user_id=user_info.get("id"),
        email=email,
        display_name=display_name,
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=int(time.time()) + int(tokens["expires_in"])
    )

    # Start background sync for this user
    background_tasks.add_task(sync_core.sync_user, email)

    return JSONResponse({"message": "User authorized and sync started", "email": email, "display_name": display_name})


