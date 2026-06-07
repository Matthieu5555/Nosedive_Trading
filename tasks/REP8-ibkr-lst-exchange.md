# REP8 — Implement the IBKR Live-Session-Token exchange (RSA/DH) on pycryptodome

> **BLOCKED — gated on IBKR live-auth work. HIGH risk (crypto) — review under `/security-review`.**
> ([AUDIT-library-leverage-2026-06-07.md](AUDIT-library-leverage-2026-06-07.md))
> The genuinely dangerous crypto — the LST acquisition — does not exist yet; it is the **only**
> place pycryptodome is actually needed and the one place hand-rolling crypto is forbidden.

- **Owns:** `packages/infra-ibkr/src/algotrading/infra_ibkr/connectivity/` —
  `cp_rest_oauth.py` (the LST exchange + nonce/timestamp source), `cp_rest_transport.py`
  (wire the `oauth_signer` callable, currently unconstructed), `cp_rest_session.py`.
- **Depends on:** the IBKR live-auth workstream (real consumer-key/private-key flow, ahead of
  execution 3B). Until then the OAuth path is a tested-but-unwired signer.
- **Blocks:** live IBKR CP REST authenticated access (historical/account endpoints behind LST).
- **State going in:** the per-request HMAC-SHA256 signer exists and is stdlib-only + well-tested
  (keep it). The LST half — RSA-SHA256 request-token signing, RSA-OAEP/PKCS1 decrypt of the
  access-token secret, Diffie-Hellman key exchange — is **absent**. No nonce/timestamp source
  exists. See [ADR 0031](../.agent/decisions/0031-ibkr-historical-data-cp-rest-oauth1a.md).

## Objective

Implement the LST acquisition using pycryptodome primitives (never hand-rolled big-int math),
and wire the signer into the transport with a CSPRNG nonce — making REP0's "built on
pycryptodome" claim finally true.

## What to do (ordered)

1. **RSA + DH on pycryptodome:** `Crypto.PublicKey.RSA`, `Crypto.Signature.pkcs1_15`
   (RSA-SHA256 request-token signature), `Crypto.Cipher.PKCS1_OAEP` / `PKCS1_v1_5` (decrypt
   the access-token secret), `Crypto.Util.number` for the DH modular exponentiation. **Never**
   a manual `pow()` / hand-rolled big-int. This is the house "don't hand-roll crypto" line.
2. **Keep** the existing stdlib HMAC-SHA256 per-request signer (`sign_hmac_sha256`,
   base64-decoded-LST key) as-is — IBKR's non-standard variant justifies it; do not swap to oauthlib.
3. **Wire `oauth_signer` into `CpRestTransport`** (`cp_rest_transport.py:42-71`, currently no
   caller constructs it) with a **CSPRNG nonce** — `secrets.token_hex` / `token_urlsafe`, never
   `random` / `uuid1` / a counter. Keep nonce/timestamp injectable for deterministic tests.
4. **No secrets in git:** consumer key, private key path, LST stay caller-supplied from
   `.env`/config (the existing `OAuthCredentials.__post_init__` empty-rejection pattern).
5. **Security review:** run `/security-review` on the diff before merge. Add a known-answer
   test vector for the LST flow as an independent oracle (mirror the existing RFC-5849 KAT).

## Done when

A live LST is acquired and per-request signing works end-to-end against IBKR CP REST; the
signer is constructed in production with a CSPRNG nonce; `/security-review` clean; KAT test
green; ADR 0031 / `cp_rest_oauth.py` text now accurately says the LST exchange is built on
pycryptodome.
