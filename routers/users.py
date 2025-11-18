import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Path, Cookie
import mysql.connector

from db import get_db
from schemas import UserOut, UpdateUserIn, RoleOut
from passlib.hash import argon2 as pwd
from security.jwt_tools import verify_token
from security.deps import COOKIE_NAME_AT
from routers.activity_logger import log_activity

router = APIRouter(tags=["Users"])


def _map_user_row(row: dict) -> UserOut:
    return UserOut(
        id=row["user_id"],
        name=row.get("username") or "",
        email=row.get("email") or "",
        role=row.get("role_name"),
    )


def _actor_id_from_cookie(access_token: str | None) -> Optional[int]:
    if not access_token:
        return None
    try:
        claims = verify_token(access_token)
        if claims.get("type") == "access":
            return int(claims["sub"])
    except Exception:
        return None
    return None


@router.get("/users", response_model=List[UserOut])
def list_users():
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT u.user_id, u.username, u.email, r.role_name
            FROM `user` u
            LEFT JOIN roles r ON r.roles_id = u.roles_id
            ORDER BY u.user_id ASC
            """
        )
        rows = cursor.fetchall()
        return [_map_user_row(r) for r in rows]
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: int = Path(..., ge=1),
    body: Optional[UpdateUserIn] = None,
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    if body is None:
        raise HTTPException(status_code=400, detail="No fields to update")

    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)

        # Check user exists
        cursor.execute(
            "SELECT user_id FROM `user` WHERE user_id=%s",
            (user_id,),
        )
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="User not found")

        set_parts: list[str] = []
        params: list = []

        # username
        if body.username is not None and body.username.strip():
            set_parts.append("username=%s")
            params.append(body.username.strip())

        # email
        if body.email is not None:
            set_parts.append("email=%s")
            params.append(body.email)

        # password
        if body.password is not None and body.password != "":
            if len(body.password) < 6:
                raise HTTPException(
                    status_code=400,
                    detail="Password must be at least 6 characters",
                )
            try:
                hashed_pw = pwd.hash(body.password)
            except Exception as e:
                logging.exception("Hashing failed")
                raise HTTPException(
                    status_code=500,
                    detail=f"Hashing failed: {e}",
                )
            set_parts.append("password=%s")
            params.append(hashed_pw)

        # role (by role name)
        if body.role is not None:
            cursor.execute(
                """
                SELECT roles_id
                FROM roles
                WHERE LOWER(TRIM(role_name)) = LOWER(%s)
                """,
                (body.role.strip(),),
            )
            r = cursor.fetchone()
            if not r:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown role: {body.role}",
                )
            set_parts.append("roles_id=%s")
            params.append(r["roles_id"])

        # roles_id (by id)
        if body.roles_id is not None:
            cursor.execute(
                "SELECT role_name FROM roles WHERE roles_id=%s",
                (body.roles_id,),
            )
            r = cursor.fetchone()
            if not r:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown roles_id: {body.roles_id}",
                )

            if "roles_id=%s" in set_parts:
                idx = set_parts.index("roles_id=%s")
                params[idx] = body.roles_id
            else:
                set_parts.append("roles_id=%s")
                params.append(body.roles_id)

        if not set_parts:
            raise HTTPException(status_code=400, detail="Nothing to update")

        sql = f"UPDATE `user` SET {', '.join(set_parts)} WHERE user_id=%s"
        params.append(user_id)

        cursor.execute(sql, tuple(params))
        conn.commit()

        # Return updated row
        cursor.execute(
            """
            SELECT u.user_id, u.username, u.email, r.role_name
            FROM `user` u
            LEFT JOIN roles r ON r.roles_id = u.roles_id
            WHERE u.user_id=%s
            """,
            (user_id,),
        )
        row = cursor.fetchone()

        # 🔔 ACTIVITY: updated account
        actor_id = _actor_id_from_cookie(access_token)
        log_activity(
            actor_id,
            "Update",
            f"Updated account user_id={user_id}.",
        )

        return _map_user_row(row)

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


@router.delete("/users/{user_id}", status_code=204)
def delete_user(
    user_id: int = Path(..., ge=1),
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME_AT),
):
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT user_id, username FROM `user` WHERE user_id=%s",
            (user_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")

        cursor.execute(
            "DELETE FROM `user` WHERE user_id=%s",
            (user_id,),
        )
        conn.commit()

        # 🔔 ACTIVITY: deleted account
        actor_id = _actor_id_from_cookie(access_token)
        log_activity(
            actor_id,
            "Delete",
            f"Deleted account user_id={user_id} ({row['username']}).",
        )

        return  # 204 No Content
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()


@router.get("/roles", response_model=List[RoleOut])
def list_roles():
    conn = get_db()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT roles_id, role_name FROM roles ORDER BY roles_id")
        rows = cursor.fetchall()
        return [RoleOut(id=row["roles_id"], name=row["role_name"]) for row in rows]
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        conn.close()
