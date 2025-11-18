import os
from datetime import datetime, timedelta, timezone
from typing import Tuple, Dict, Any
from jose import jwt, JWTError

JWT_SECRET = os.getenv("JWT_SECRET", "dev_only")
ALGO = "HS256"
ACCESS_MIN = int(os.getenv("ACCESS_TOKEN_TTL_MIN", "15"))
REFRESH_DAYS = int(os.getenv("REFRESH_TOKEN_TTL_DAYS", "7"))

def _exp_in(minutes: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(minutes=minutes)).timestamp())

def _exp_days(days: int) -> int:
    return int((datetime.now(timezone.utc) + timedelta(days=days)).timestamp())

def sign_access(user_id: int, role: str) -> Tuple[str, int]:
    payload = {"sub": str(user_id), "role": role, "type": "access", "exp": _exp_in(ACCESS_MIN)}
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGO), payload["exp"]

def sign_refresh(user_id: int, role: str) -> Tuple[str, int]:
    payload = {"sub": str(user_id), "role": role, "type": "refresh", "exp": _exp_days(REFRESH_DAYS)}
    return jwt.encode(payload, JWT_SECRET, algorithm=ALGO), payload["exp"]

def verify_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[ALGO])
    except JWTError as e:
        raise ValueError(str(e))
