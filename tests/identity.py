"""The test suite's identity provider: one RS256 key pair, the verifier the app trusts, and the
tokens it signs. Every test that calls the API authenticates through here, so a change to how
callers prove who they are changes this file and nothing else."""

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.auth import RateLimiter, TokenVerifier
from app.settings import Settings

DEFAULTS = Settings()
"""Issuer and audiences as the app reads them, before any test overrides."""


class Identity:
    """Signs tokens the app accepts; `forger()` signs with a key it does not."""

    def __init__(self, kid: str = "test-1") -> None:
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = {**json.loads(RSAAlgorithm.to_jwk(self.key.public_key())), "kid": kid}
        self.jwks = {"keys": [{**jwk, "alg": "RS256", "use": "sig"}]}
        self.verifier = TokenVerifier(jwt.PyJWKSet.from_dict(self.jwks), DEFAULTS.auth_issuer)

    def token(self, subject: str, audience: str, ttl: int = 300, **claims) -> str:
        now = int(time.time())
        body = {"iss": DEFAULTS.auth_issuer, "sub": subject, "aud": audience, "iat": now}
        body |= {"exp": now + ttl, **claims}
        return jwt.encode(body, self.key, algorithm="RS256", headers={"kid": self.kid})

    def merchant(self, subject: str, **claims) -> dict[str, str]:
        """Headers of a request from this merchant."""
        token = self.token(subject, DEFAULTS.merchant_audience, **claims)
        return {"Authorization": f"Bearer {token}"}

    def operator(self, subject: str = "ana", role: str = "operator") -> dict[str, str]:
        """Headers of a request from this operator, in this role."""
        token = self.token(subject, DEFAULTS.operator_audience, role=role)
        return {"Authorization": f"Bearer {token}"}


IDENTITY = Identity()
FORGER = Identity()
"""Same kid, another key: what the app must reject."""


def serve(api, settings, storage, graph, retriever) -> None:
    """Install on `api` what its lifespan would have: the store, the graph, the index, and this
    identity provider's keys."""
    api.state.settings, api.state.retriever, api.state.graph = settings, retriever, graph
    api.state.queue, api.state.locks, api.state.audit = storage.queue, storage.locks, storage.audit
    api.state.desk = storage.desk
    api.state.verifier, api.state.limiter = IDENTITY.verifier, RateLimiter()
