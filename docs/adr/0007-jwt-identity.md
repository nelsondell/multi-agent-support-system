# ADR 0007: Identity from a verified JWT, roles for operators (T-49, accepted)

**Context.** V4 scored Security & Identity 9.3: the merchant id came in the request body, and
operators shared one token.

**Decision.**
- **Merchants** send an RS256 JWT, checked for issuer and audience against the provider's JWKS
  (`AUTH_JWKS_URL`).
- **The merchant id is only the token's `sub`.** Tools get it from graph state through
  `InjectedState`, and no schema the model sees has the field.
- **Operators** have individual tokens with a role, `operator` or `supervisor`.
- **Every operator action and every write** goes to an append-only audit log.
- **Rate limit:** per subject, with 429 and `Retry-After`.

**Evidence.**
- `tests/test_security.py`: forged, expired and wrong-audience tokens; the role matrix; erasure;
  the audit log.
- `tests/test_tools.py`: `user_id` is not in the model's schema.

**Considered.** API keys per merchant (no expiry or rotation story); session cookies (this is an
API); mTLS (complementary, at the infrastructure).

**Limit.** The dev identity provider is a local key pair (`make dev-keys`); production points
`AUTH_JWKS_URL` at a real one.
