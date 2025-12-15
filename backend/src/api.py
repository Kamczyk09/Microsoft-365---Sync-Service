from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sync_db import SyncDB
from models import UserResponse

DB_PATH = "/project/backend/db/sync.db"

app = FastAPI(title="Thalamind Sync API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # lock down later
    allow_methods=["*"],
    allow_headers=["*"],
)

db = SyncDB(DB_PATH)


@app.get("/users", response_model=list[UserResponse])
def list_users():
    return db.list_users()
