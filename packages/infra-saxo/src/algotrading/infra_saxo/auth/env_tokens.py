"""Pure .env token upsert: set keys in a .env text body without touching unrelated lines.

Kept I/O-free so it is trivially testable and reusable: the caller owns reading and writing the
file. Saxo rotates the refresh token on every refresh and invalidates the previous one, so a
long-running session must persist each rotation to keep the .env restart-resilient.
"""

from __future__ import annotations


def upsert_env_vars(env_text: str, updates: dict[str, str]) -> str:
    """Return ``env_text`` with each key in ``updates`` set, preserving all other lines.

    Existing keys are replaced in place; new keys are appended. Comment and blank lines, and any
    key not in ``updates``, are left untouched.
    """
    lines = env_text.splitlines()
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    return "\n".join(out) + "\n"
