"""Who is calling, proven by an RS256 token from AUTH_JWKS_URL, never by a field in the body."""

import json
import math
import time
from collections import deque
from pathlib import Path
from typing import Annotated, Literal

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from app.settings import Settings

Role = Literal["operator", "supervisor"]


class Principal(BaseModel):
    subject: str
    role: Role


class TokenVerifier:
    def __init__(self, keys: jwt.PyJWKSet, issuer: str) -> None:
        self._keys, self._issuer = keys, issuer

    @classmethod
    def from_settings(cls, settings: Settings) -> "TokenVerifier":
        """Loads the keys once, at start-up; without them the API refuses to start."""
        source = settings.auth_jwks_url
        if not source:
            raise ValueError("AUTH_JWKS_URL is not set; run `make dev-keys` for a local key pair")
        if source.startswith(("https://", "http://")):
            document = httpx.get(source, timeout=10).raise_for_status().json()
        else:
            document = json.loads(Path(source).read_text(encoding="utf-8"))
        return cls(jwt.PyJWKSet.from_dict(document), settings.auth_issuer)

    def claims(self, token: str, audience: str) -> dict:
        kid = jwt.get_unverified_header(token).get("kid")
        key = self._keys[kid] if kid else self._keys.keys[0]
        return jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=audience,
            issuer=self._issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )


class RateLimiter:
    """At most `per_minute` requests per subject in any 60 s, counted per replica."""

    def __init__(self, per_minute: int = 30) -> None:
        self._limit = per_minute
        self._seen: dict[str, deque[float]] = {}

    def retry_after(self, subject: str) -> int | None:
        """Seconds until the subject may call again, or None, counting this call."""
        now = time.monotonic()
        window = self._seen.setdefault(subject, deque())
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= self._limit:
            return math.ceil(60 - (now - window[0]))
        window.append(now)
        return None


def _claims(request: Request, audience: str) -> dict:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "a bearer token is required", {"WWW-Authenticate": "Bearer"})
    try:
        return request.app.state.verifier.claims(token, audience)
    except (jwt.PyJWTError, KeyError) as exc:
        raise HTTPException(401, "invalid token", {"WWW-Authenticate": "Bearer"}) from exc


def merchant(request: Request) -> str:
    """The merchant id is the token's `sub`, the only place it comes from."""
    subject = _claims(request, request.app.state.settings.merchant_audience)["sub"]
    wait = request.app.state.limiter.retry_after(subject)
    if wait is not None:
        raise HTTPException(429, "too many requests", {"Retry-After": str(wait)})
    return subject


def operator(request: Request) -> Principal:
    claims = _claims(request, request.app.state.settings.operator_audience)
    if claims.get("role") not in ("operator", "supervisor"):
        raise HTTPException(403, "an operator or supervisor role is required")
    return Principal(subject=claims["sub"], role=claims["role"])


def supervisor(principal: Annotated[Principal, Depends(operator)]) -> Principal:
    if principal.role != "supervisor":
        raise HTTPException(403, "a supervisor role is required")
    return principal


Merchant = Annotated[str, Depends(merchant)]
Operator = Annotated[Principal, Depends(operator)]
Supervisor = Annotated[Principal, Depends(supervisor)]
