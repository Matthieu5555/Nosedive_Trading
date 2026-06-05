"""In-memory CSRF state store for an OAuth web flow.

``generate()`` mints a cryptographically random state token and records its wall-clock
expiry; ``consume()`` validates and removes it in one atomic step so a token can never be
reused. Expired tokens are pruned lazily on every access. Self-contained (stdlib only).

This is the verifiable half of the Saxo OAuth flow. The other half — exchanging the code
for tokens against Saxo and injecting them into a live session — needs the Saxo broker
backend, which is not in the flat backend yet (it arrives with ``packages/infra-saxo``).
Until then the callback validates state here and reports the backend as not configured.
"""

from __future__ import annotations

import secrets
import threading
import time

_TTL_SECONDS = 300.0  # 5 minutes — generous for a human redirect round-trip.


class OAuthStateStore:
    """Thread-safe single-use CSRF state token store."""

    def __init__(self, ttl: float = _TTL_SECONDS) -> None:
        self._ttl = ttl
        self._lock = threading.Lock()
        self._tokens: dict[str, float] = {}  # token -> monotonic expiry

    def generate(self) -> str:
        """Mint a new random state token, record its expiry, and return it."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._tokens[token] = time.monotonic() + self._ttl
        return token

    def consume(self, token: str) -> bool:
        """Validate and remove ``token`` in one step; True iff valid and not expired."""
        with self._lock:
            self._prune()
            return self._tokens.pop(token, None) is not None

    def _prune(self) -> None:
        now = time.monotonic()
        for token in [t for t, expiry in self._tokens.items() if expiry <= now]:
            del self._tokens[token]


# Module-level singleton shared across the app.
_store = OAuthStateStore()


def generate_state() -> str:
    return _store.generate()


def consume_state(token: str) -> bool:
    return _store.consume(token)
