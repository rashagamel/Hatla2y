"""
Authentication module for Tasahel.

Handles:
  - Password hashing (bcrypt)
  - JWT token generation / verification
  - User CRUD
  - Role-based access control helpers

Roles:
  agent   — sees only leads assigned to them
  manager — sees all leads, can reassign, sees full analytics
  admin   — same as manager + can manage users
"""
import os
import json
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from db import get_connection

def _auth_required():
    return os.environ.get("TASAHEL_REQUIRE_AUTH", "0").lower() in ("1", "true", "yes")


def _load_secret_key():
    """In production (TASAHEL_REQUIRE_AUTH=1) a stable SECRET_KEY is mandatory: a random
    per-process key logs everyone out on every restart and breaks multi-worker servers
    (a token signed by one worker is rejected by the other)."""
    key = os.environ.get("SECRET_KEY", "")
    if _auth_required():
        if len(key) < 32:
            raise RuntimeError("SECRET_KEY must be set to a random string of at least 32 characters "
                               "when TASAHEL_REQUIRE_AUTH=1")
        return key
    return key or secrets.token_hex(32)


SECRET_KEY = _load_secret_key()
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 8
VALID_ROLES = ("agent", "manager", "admin")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_BYTES = 72          # bcrypt ignores/rejects anything longer


def _validate_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")


def _validate_role(role):
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {list(VALID_ROLES)}")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def generate_token(user_id: int, email: str, role: str) -> str:
    payload = {
        "sub": str(user_id),          # PyJWT >= 2.10 rejects a non-string subject
        "email": email,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    """Returns payload dict or raises jwt.ExpiredSignatureError / jwt.InvalidTokenError."""
    return jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# Flask request helper — extracts + validates token from Authorization header
# ---------------------------------------------------------------------------

def get_current_user(request):
    """
    Extracts the JWT from the Authorization: Bearer <token> header.
    Returns the user row dict on success.
    Raises ValueError with a human-readable message on failure.
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Missing or malformed Authorization header")

    token = auth_header[len("Bearer "):]
    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise ValueError("Token expired — please log in again")
    except (jwt.InvalidTokenError, ValueError, TypeError) as e:
        raise ValueError(f"Invalid token: {e}")

    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE id = ? AND is_active = 1", (int(payload["sub"]),)
    ).fetchone()
    conn.close()

    if not user:
        raise ValueError("User not found or deactivated")
    return dict(user)


def require_auth(request):
    """Returns (user_dict, None) on success, or (None, error_response_tuple) on failure."""
    from flask import jsonify
    try:
        user = get_current_user(request)
        return user, None
    except ValueError as e:
        return None, (jsonify({"error": str(e)}), 401)


def require_manager(request):
    """Like require_auth but also enforces manager/admin role."""
    from flask import jsonify
    user, err = require_auth(request)
    if err:
        return user, err
    if user["role"] not in ("manager", "admin"):
        return None, (jsonify({"error": "Manager or admin role required"}), 403)
    return user, None


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def create_user(email: str, password: str, name: str, role: str = "agent", agent_id: int = None) -> dict:
    """Creates a new user. Raises ValueError if email already exists."""
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise ValueError("A valid email is required")
    if not (name or "").strip():
        raise ValueError("name is required")
    _validate_role(role)
    _validate_password(password)
    conn = get_connection()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        conn.close()
        raise ValueError(f"Email {email} is already registered")

    pw_hash = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users (email, password_hash, name, role, agent_id) VALUES (?, ?, ?, ?, ?)",
        (email, pw_hash, name.strip(), role, agent_id),
    )
    conn.commit()
    user_id = cur.lastrowid
    user = dict(conn.execute("SELECT id, email, name, role, agent_id, is_active, created_at FROM users WHERE id = ?", (user_id,)).fetchone())
    conn.close()
    return user


_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()


def authenticate_user(email: str, password: str) -> dict:
    """Returns user dict + token on success. Raises ValueError on failure."""
    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ? AND is_active = 1", ((email or "").strip().lower(),)
    ).fetchone()

    # always run one bcrypt check so a wrong email and a wrong password take the same time
    stored_hash = user["password_hash"] if user else _DUMMY_HASH
    password_ok = verify_password(password, stored_hash)
    if not user or not password_ok:
        conn.close()
        raise ValueError("Invalid email or password")

    # update last_login
    conn.execute("UPDATE users SET last_login = datetime('now') WHERE id = ?", (user["id"],))
    conn.commit()
    conn.close()

    user_dict = dict(user)
    token = generate_token(user_dict["id"], user_dict["email"], user_dict["role"])
    return {
        "token": token,
        "user": {
            "id": user_dict["id"],
            "email": user_dict["email"],
            "name": user_dict["name"],
            "role": user_dict["role"],
            "agent_id": user_dict["agent_id"],
        },
        "expires_in": TOKEN_EXPIRY_HOURS * 3600,
    }


def list_users() -> list:
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, email, name, role, agent_id, is_active, last_login, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_user(user_id: int, updates: dict) -> dict:
    """Update allowed fields: name, role, agent_id, is_active. Password handled separately."""
    allowed = {"name", "role", "agent_id", "is_active"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        raise ValueError("No valid fields to update")
    if "role" in filtered:
        _validate_role(filtered["role"])
    if "is_active" in filtered:
        filtered["is_active"] = 1 if filtered["is_active"] else 0

    conn = get_connection()
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", (*filtered.values(), user_id))
    conn.commit()
    user = conn.execute(
        "SELECT id, email, name, role, agent_id, is_active, last_login, created_at FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()
    conn.close()
    if not user:
        raise ValueError("User not found")
    return dict(user)


def change_password(user_id: int, current_password: str, new_password: str):
    conn = get_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        conn.close()
        raise ValueError("User not found")
    if not verify_password(current_password, user["password_hash"]):
        conn.close()
        raise ValueError("Current password is incorrect")
    _validate_password(new_password)
    new_hash = hash_password(new_password)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    conn.commit()
    conn.close()


def seed_default_users():
    """Creates the first accounts when the users table is empty.

    Production (TASAHEL_REQUIRE_AUTH=1): no well-known passwords are ever created. Set
    TASAHEL_ADMIN_EMAIL + TASAHEL_ADMIN_PASSWORD and the first start creates that one manager.
    Local demo (auth not required): the old demo accounts are created for convenience."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    if count > 0:
        return  # already seeded

    if _auth_required():
        email = os.environ.get("TASAHEL_ADMIN_EMAIL", "").strip()
        password = os.environ.get("TASAHEL_ADMIN_PASSWORD", "")
        if not email or not password:
            print("[auth] No users exist and TASAHEL_ADMIN_EMAIL / TASAHEL_ADMIN_PASSWORD are not set. "
                  "Nobody will be able to log in. Set both and restart (first start only).")
            return
        create_user(email, password, "Admin", role="admin")
        print(f"[auth] Created first admin account: {email}")
        return

    print("[auth] No users found - creating DEMO accounts (local use only)...")
    create_user("manager@tasahel.com", "manager123", "Sales Manager", role="manager")
    create_user("agent@tasahel.com", "agent123", "Demo Agent", role="agent")
    print("  manager@tasahel.com / manager123  (role: manager)")
    print("  agent@tasahel.com   / agent123    (role: agent)")
    print("  [!] Demo passwords. Never run this way on a public server.")