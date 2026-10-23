"""The suite's identity provider: every test that calls the API authenticates through here."""

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from app.auth import RateLimiter, TokenVerifier
from app.settings import Settings

DEFAULTS = Settings()  # issuer and audiences as the app reads them


class Identity:
    """An RS256 key pair, the verifier that trusts it, and the tokens it signs."""

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
        return {
            "Authorization": f"Bearer {self.token(subject, DEFAULTS.merchant_audience, **claims)}"
        }

    def operator(self, subject: str = "ana", role: str = "operator") -> dict[str, str]:
        token = self.token(subject, DEFAULTS.operator_audience, role=role)
        return {"Authorization": f"Bearer {token}"}


IDENTITY = Identity()
FORGER = Identity()  # same kid, another key: what the app must reject


def serve(api, settings, storage, graph, retriever) -> None:
    """Install on `api` what its lifespan would: the stores, the graph, the index and these keys."""
    api.state.settings, api.state.retriever, api.state.graph = settings, retriever, graph
    api.state.queue, api.state.locks, api.state.audit = storage.queue, storage.locks, storage.audit
    api.state.desk, api.state.verifier = storage.desk, IDENTITY.verifier
    api.state.limiter = RateLimiter(settings.rate_limit_per_minute)
