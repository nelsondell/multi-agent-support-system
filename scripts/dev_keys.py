"""`make dev-keys`: a local RS256 key pair standing in for the identity provider.

Writes `.dev/jwt-private.pem` (kept to this machine; `.dev/` is ignored by git) and
`.dev/jwks.json`, the public key the API reads through AUTH_JWKS_URL. Existing keys are kept, so
tokens already minted stay valid; delete `.dev/` to rotate.
"""

import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

KEYS = Path(__file__).resolve().parent.parent / ".dev"
KID = "dev-1"


def main() -> int:
    private, public = KEYS / "jwt-private.pem", KEYS / "jwks.json"
    if private.exists() and public.exists():
        print(f"keeping the keys in {KEYS}")
        return 0
    KEYS.mkdir(exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    private.write_bytes(pem)
    private.chmod(0o600)
    jwk = {**json.loads(RSAAlgorithm.to_jwk(key.public_key())), "kid": KID, "use": "sig"}
    public.write_text(json.dumps({"keys": [{**jwk, "alg": "RS256"}]}, indent=2) + "\n")
    print(f"wrote {private.name} and {public.name} to {KEYS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
