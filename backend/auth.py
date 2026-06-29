"""
auth.py — JWT authentication for Lumin.

Flow:
  POST /auth/register  → create user (username + password)
  POST /auth/login     → returns access_token (JWT)
  All protected routes → require Bearer token in Authorization header

Users are stored in a local SQLite file (auth.db) separate from printer_data.db.
In production, replace with a proper user store.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# ── config ────────────────────────────────────────────────────────────────────

SECRET_KEY = os.environ.get("LUMIN_SECRET_KEY", "change-this-in-production-please")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("TOKEN_EXPIRE_MINUTES", "60"))

AUTH_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "auth.db")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()

# ── user store (SQLite) ───────────────────────────────────────────────────────

def _get_auth_conn():
    conn = sqlite3.connect(AUTH_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_auth_db():
    """Create users table if it doesn't exist. Called on startup."""
    os.makedirs(os.path.dirname(AUTH_DB_PATH), exist_ok=True)
    conn = _get_auth_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT UNIQUE NOT NULL,
            hashed_pw TEXT NOT NULL,
            created   TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def get_user(username: str) -> dict | None:
    conn = _get_auth_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_user(username: str, password: str) -> dict:
    """Create a new user. Raises ValueError if username already exists."""
    if get_user(username):
        raise ValueError(f"Username '{username}' is already taken.")
    hashed = pwd_context.hash(password)
    conn = _get_auth_conn()
    conn.execute(
        "INSERT INTO users (username, hashed_pw, created) VALUES (?, ?, ?)",
        (username, hashed, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return {"username": username}


# ── token helpers ─────────────────────────────────────────────────────────────

def create_access_token(username: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str:
    """Decode token, return username. Raises HTTPException on failure."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if not username:
            raise credentials_error
        return username
    except JWTError:
        raise credentials_error


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)) -> str:
    """
    Dependency — inject into any route to require authentication.
    Returns the username on success.
    """
    return verify_token(credentials.credentials)

class AuthRequest(BaseModel):
    username: str
    password: str

@router.post("/register")
def register(body: AuthRequest):
    try:
        user = create_user(body.username, body.password)
        return {"message": "User created.", "username": user["username"]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/login")
def login(body: AuthRequest):
    user = get_user(body.username)
    if not user or not pwd_context.verify(body.password, user["hashed_pw"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    token = create_access_token(body.username)
    return {"access_token": token, "token_type": "bearer"}