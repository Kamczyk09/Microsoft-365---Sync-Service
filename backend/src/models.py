from pydantic import BaseModel
from typing import Optional


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    expires_at: int
    last_sync: Optional[int]
    item_count: int
