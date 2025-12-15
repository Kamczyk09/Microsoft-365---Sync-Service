from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.sync_db import SyncDB
from src.models import UserResponse

DB_PATH = "/project/backend/db/sync.db"

app = FastAPI(title="Thalamind Sync API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)

# Do NOT reuse the same connection object across threads
def get_db():
    return SyncDB(DB_PATH)


@app.get("/users", response_model=list[UserResponse])
def list_users():
    db = get_db()
    return db.list_users()
