"""Mint a token with the `make dev-keys` key, for local runs and the demo.

    uv run python scripts/dev_token.py merchant cliente1988
    uv run python scripts/dev_token.py operator ana
    uv run python scripts/dev_token.py supervisor bia

A merchant token carries MERCHANT_AUDIENCE and the merchant id in `sub`; an operator token carries
OPERATOR_AUDIENCE and its role. Issuer and audiences come from the same settings the API reads.
"""

import argparse
import sys
import time
from pathlib import Path

import jwt

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.settings import get_settings  # noqa: E402

KEYS = Path(__file__).resolve().parent.parent / ".dev"


def mint(kind: str, subject: str, ttl: int) -> str:
    settings = get_settings()
    now = int(time.time())
    claims = {"iss": settings.auth_issuer, "sub": subject, "iat": now, "exp": now + ttl}
    if kind == "merchant":
        claims["aud"] = settings.merchant_audience
    else:
        claims |= {"aud": settings.operator_audience, "role": kind}
    key = (KEYS / "jwt-private.pem").read_bytes()
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "dev-1"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=["merchant", "operator", "supervisor"])
    parser.add_argument("subject")
    parser.add_argument("--ttl", type=int, default=3600, help="seconds until it expires")
    arguments = parser.parse_args()
    print(mint(arguments.kind, arguments.subject, arguments.ttl))
    return 0


if __name__ == "__main__":
    sys.exit(main())
