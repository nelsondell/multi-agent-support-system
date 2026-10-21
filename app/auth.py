"""Who is calling, proven by a signed token: never by a field the client fills in.

Merchants and operators send `Authorization: Bearer <JWT>`, RS256, signed by the identity
provider whose public keys are at `AUTH_JWKS_URL` (an https URL, or a file that `make dev-keys`
writes). Issuer, audience and expiry are checked. Merchant tokens carry `MERCHANT_AUDIENCE` and
the merchant id in `sub`, the only place it comes from; the graph still receives it through
InjectedState. Operator tokens carry `OPERATOR_AUDIENCE` and a `role`: `operator` acts on cases,
`supervisor` may also read the audit log. A merchant is held to `RATE_LIMIT_PER_MINUTE` requests.
"""

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
    """An authenticated operator: who, and what they may do."""

    subject: str
    role: Role


class TokenVerifier:
    """Checks a token against the provider's public keys, issuer and an audience."""

    def __init__(self, keys: jwt.PyJWKSet, issuer: str) -> None:
        self._keys, self._issuer = keys, issuer

    @classmethod
    def from_settings(cls, settings: Settings) -> "TokenVerifier":
        """Load the public keys once, at start-up, failing fast without them."""
        source = settings.auth_jwks_url
        if not source:
            raise ValueError("AUTH_JWKS_URL is not set; run `make dev-keys` for a local key pair")
        if source.startswith(("https://", "http://")):
            document = httpx.get(source, timeout=10).raise_for_status().json()
        else:
            document = json.loads(Path(source).read_text(encoding="utf-8"))
        return cls(jwt.PyJWKSet.from_dict(document), settings.auth_issuer)

    def claims(self, token: str, audience: str) -> dict:
        """The token's claims, or `jwt.PyJWTError` for a forged, expired or misaddressed one."""
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
    """At most `per_minute` requests per subject in any 60 seconds, counted on this replica."""

    def __init__(self, per_minute: int = 30) -> None:
        self._limit = per_minute
        self._seen: dict[str, deque[float]] = {}

    def retry_after(self, subject: str) -> int | None:
        """Seconds until this subject may call again, or None, counting this call, when it may."""
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
    """The calling merchant's id, from a merchant token's `sub`, within its rate limit."""
    subject = _claims(request, request.app.state.settings.merchant_audience)["sub"]
    wait = request.app.state.limiter.retry_after(subject)
    if wait is not None:
        raise HTTPException(429, "too many requests", {"Retry-After": str(wait)})
    return subject


def operator(request: Request) -> Principal:
    """The calling operator, from an operator token with a known role."""
    claims = _claims(request, request.app.state.settings.operator_audience)
    if claims.get("role") not in ("operator", "supervisor"):
        raise HTTPException(403, "an operator or supervisor role is required")
    return Principal(subject=claims["sub"], role=claims["role"])


def supervisor(principal: Annotated[Principal, Depends(operator)]) -> Principal:
    """An operator whose role is supervisor."""
    if principal.role != "supervisor":
        raise HTTPException(403, "a supervisor role is required")
    return principal


Merchant = Annotated[str, Depends(merchant)]
Operator = Annotated[Principal, Depends(operator)]
Supervisor = Annotated[Principal, Depends(supervisor)]
