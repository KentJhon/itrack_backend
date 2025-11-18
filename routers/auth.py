import logging
import os

from typing import Optional
from fastapi import APIRouter, HTTPException, Form, Response, Cookie
from pydantic import EmailStr
import mysql.connector

from security.jwt_tools import sign_access, sign_refresh, verify_token
from security.deps import COOKIE_NAME_AT, COOKIE_NAME_RT
from routers.activity_logger import log_activity   # 👈 use helper

from passlib.hash import argon2 as pwd  # keep your chosen hasher
from db import get_db

router = APIRouter(tags=["auth"])
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", "localhost")


def _set_cookie(resp: Response, name: str, value: str, expires_unix: int):
    resp.set_cookie(
        key=name,
        value=value,
        httponly=True,
        samesite="lax",
        secure=False,
        expires=expires_unix,
        path="/",
    )


def _clear_cookie(resp: Response, name: str):
    resp.delete_cookie(key=name, path="/")


def _user_id_from_access_cookie(access_token: str | None) -> Optional[int]:
    if not access_token:
        return None
    try:
        claims = verify_token(access_token)
        if claims.get("type") == "access":
            return int(claims["sub"])
    except Exception:
        return None
    return None


@router.post("/register")
def register(
    username: str = Form(...),
    email: EmailStr = Form(...),
    password: str = Form(...),
    role: Optional[str] = Form(None),
    roles_id: Optional[int] = Form(None),
):
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    try:
        hashed_pw = pwd.hash(password)
    except Exception as e:
        logging.exception("Hashing failed")
        raise HTTPException(status_code=500, detail=f"Hashing failed: {e}")

    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)

        logging.info(f"/register received role={role!r}, roles_id={roles_id!r}")

        # Resolve role id
        resolved_role_id = None
        if roles_id is not None:
            cursor.execute("SELECT role_name FROM roles WHERE roles_id=%s", (roles_id,))
            r = cursor.fetchone()
            if not r:
                raise HTTPException(status_code=400, detail=f"Unknown roles_id: {roles_id}")
            resolved_role_id = roles_id
        elif role:
            role_clean = role.strip()
            cursor.execute(
                "SELECT roles_id FROM roles WHERE LOWER(TRIM(role_name)) = LOWER(%s)",
                (role_clean,),
            )
            r = cursor.fetchone()
            if not r:
                raise HTTPException(status_code=400, detail=f"Unknown role: {role}")
            resolved_role_id = r["roles_id"]
        else:
            cursor.execute("SELECT roles_id FROM roles WHERE LOWER(role_name)='admin'")
            r = cursor.fetchone()
            resolved_role_id = r["roles_id"] if r else 1

        logging.info(f"/register resolved_role_id={resolved_role_id}")

        cursor.execute(
            "INSERT INTO `user` (roles_id, username, email, password) VALUES (%s, %s, %s, %s)",
            (resolved_role_id, username, email, hashed_pw),
        )
        conn.commit()
        new_id = cursor.lastrowid

        cursor.execute(
            """
            SELECT u.user_id, u.username, u.email, r.role_name
            FROM `user` u
            LEFT JOIN roles r ON r.roles_id = u.roles_id
            WHERE u.user_id=%s
            """,
            (new_id,),
        )
        row = cursor.fetchone()

        # 🔔 ACTIVITY: created account
        log_activity(
            new_id,
            "Create",
            "User was added",
        )

        return {
            "message": "User registered successfully",
            "user": {
                "id": row["user_id"],
                "name": row["username"],
                "email": row["email"],
                "role": row["role_name"],
            },
        }

    except mysql.connector.Error as err:
        logging.exception("DB error")
        if getattr(err, "errno", None) == 1062:
            raise HTTPException(status_code=409, detail="Email already exists")
        raise HTTPException(status_code=400, detail=f"MySQL error: {err}")
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()


@router.post("/login")
def login(resp: Response, username: str = Form(...), password: str = Form(...)):
    conn = get_db()
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT u.user_id, u.username, u.email, u.password, r.role_name AS role
            FROM `user` u
            LEFT JOIN roles r ON r.roles_id = u.roles_id
            WHERE u.email=%s
            """,
            (username,),
        )
        user = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()

    if not user or not pwd.verify(password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access, aexp = sign_access(user["user_id"], user["role"])
    refresh, rexp = sign_refresh(user["user_id"], user["role"])

    _set_cookie(resp, COOKIE_NAME_AT, access, aexp)
    _set_cookie(resp, COOKIE_NAME_RT, refresh, rexp)

    # 🔔 ACTIVITY: login
    log_activity(
        user["user_id"],
        "Login",
        f"User {user['username']} logged in.",
    )

    return {
        "message": "Login successful",
        "user": {
            "id": user["user_id"],
            "username": user["username"],
            "email": user["email"],
            "role": user["role"],
        },
    }


@router.post("/refresh")
def refresh(resp: Response, refresh_token: str | None = Cookie(default=None, alias=COOKIE_NAME_RT)):
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Missing refresh token")
    try:
        claims = verify_token(refresh_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid/expired refresh token")
    if claims.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Wrong token type")

    user_id = int(claims["sub"])
    role = claims.get("role") or "User"
    access, aexp = sign_access(user_id, role)
    new_refresh, rexp = sign_refresh(user_id, role)

    _set_cookie(resp, COOKIE_NAME_AT, access, aexp)
    _set_cookie(resp, COOKIE_NAME_RT, new_refresh, rexp)
    return {"message": "refreshed"}


@router.post("/logout")
def logout(
    resp: Response,
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    """
    Clear both cookies (access and refresh) and log Logout.
    """
    user_id = _user_id_from_access_cookie(access_token)

    _clear_cookie(resp, COOKIE_NAME_AT)
    _clear_cookie(resp, COOKIE_NAME_RT)

    # 🔔 ACTIVITY: logout
    log_activity(
        user_id,
        "Logout",
        "User logged out.",
    )

    return {"message": "Logged out"}


@router.get("/me")
def me(access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT)):
    if not access_token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = verify_token(access_token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid/expired access token")
    if claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type")
    return {"sub": claims["sub"], "role": claims.get("role")}
