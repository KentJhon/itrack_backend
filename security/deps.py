from typing import Optional, List, Dict, Any
from fastapi import Depends, HTTPException, status, Cookie
from .jwt_tools import verify_token

COOKIE_NAME_AT = "access_token"
COOKIE_NAME_RT = "refresh_token"

def get_current_claims(access_token: Optional[str] = Cookie(default=None, alias=COOKIE_NAME_AT)) -> Dict[str, Any]:
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing access token")
    try:
        claims = verify_token(access_token)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid/expired access token")
    if claims.get("type") != "access":
        raise HTTPException(status_code=401, detail="Wrong token type")
    return claims

def require_roles(allowed: List[str]):
    allowed_norm = [r.strip().lower() for r in allowed]
    def _checker(claims: Dict[str, Any] = Depends(get_current_claims)) -> Dict[str, Any]:
        role = (claims.get("role") or "").strip().lower()
        if role not in allowed_norm:
            raise HTTPException(status_code=403, detail="Forbidden")
        return claims
    return _checker
