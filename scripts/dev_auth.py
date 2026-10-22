"""A local identity provider: an RS256 key pair in `.dev/` (git-ignored), and tokens it signs.

uv run python scripts/dev_auth.py keys                     # make dev-keys; keeps existing keys
uv run python scripts/dev_auth.py token merchant cliente1988
uv run python scripts/dev_auth.py token operator ana       # or supervisor
"""

import argparse
import json
import sys
import time
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.settings import get_settings  # noqa: E402

KEYS, KID = ROOT / ".dev", "dev-1"
PRIVATE, PUBLIC = KEYS / "jwt-private.pem", KEYS / "jwks.json"


def keys() -> str:
    if PRIVATE.exists() and PUBLIC.exists():
        return f"keeping the keys in {KEYS}"
    KEYS.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pkcs8 = serialization.PrivateFormat.PKCS8
    PRIVATE.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, pkcs8, serialization.NoEncryption())
    )
    PRIVATE.chmod(0o600)
    jwk = {
        **json.loads(RSAAlgorithm.to_jwk(key.public_key())),
        "kid": KID,
        "use": "sig",
        "alg": "RS256",
    }
    PUBLIC.write_text(json.dumps({"keys": [jwk]}, indent=2) + "\n")
    return f"wrote {PRIVATE.name} and {PUBLIC.name} to {KEYS}"


def token(kind: str, subject: str, ttl: int) -> str:
    settings, now = get_settings(), int(time.time())
    claims = {"iss": settings.auth_issuer, "sub": subject, "iat": now, "exp": now + ttl}
    if kind == "merchant":
        claims["aud"] = settings.merchant_audience
    else:
        claims |= {"aud": settings.operator_audience, "role": kind}
    return jwt.encode(claims, PRIVATE.read_bytes(), algorithm="RS256", headers={"kid": KID})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("keys")
    minted = commands.add_parser("token")
    minted.add_argument("kind", choices=["merchant", "operator", "supervisor"])
    minted.add_argument("subject")
    minted.add_argument("--ttl", type=int, default=3600, help="seconds until it expires")
    arguments = parser.parse_args()
    if arguments.command == "keys":
        print(keys())
    else:
        print(token(arguments.kind, arguments.subject, arguments.ttl))


if __name__ == "__main__":
    main()
