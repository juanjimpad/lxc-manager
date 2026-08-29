"""HMAC auth for homelab-client → manager. The cluster key never travels
in the clear as a Bearer token: every request signs

    timestamp \\n nonce \\n METHOD \\n path \\n body

with HMAC-SHA256. Replay window is 5 minutes; nonce is unique per call
(in-process set — fail2ban remains the durable layer on /login, not here).
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections import OrderedDict

from fastapi import Header, HTTPException, Request

from . import cluster

MAX_SKEW_S = 300
_NONCE_KEEP = 4096
_seen_nonces: OrderedDict[str, float] = OrderedDict()


def sign(key: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    msg = f"{timestamp}\n{nonce}\n{method.upper()}\n{path}\n".encode() + body
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def _remember_nonce(nonce: str) -> bool:
    now = time.time()
    stale = [n for n, ts in _seen_nonces.items() if now - ts > MAX_SKEW_S]
    for n in stale:
        _seen_nonces.pop(n, None)
    if nonce in _seen_nonces:
        return False
    _seen_nonces[nonce] = now
    while len(_seen_nonces) > _NONCE_KEEP:
        _seen_nonces.popitem(last=False)
    return True


async def require_client(
    request: Request,
    x_homelab_timestamp: str = Header(...),
    x_homelab_nonce: str = Header(...),
    x_homelab_signature: str = Header(...),
    x_homelab_client: str = Header(...),
) -> str:
    """Returns the client id from the header after verifying the HMAC."""
    key = cluster.load_key()
    if not key:
        raise HTTPException(status_code=503, detail="cluster key not ready")
    try:
        ts = int(x_homelab_timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="bad timestamp") from exc
    if abs(time.time() - ts) > MAX_SKEW_S:
        raise HTTPException(status_code=401, detail="timestamp skew")
    if not x_homelab_nonce or len(x_homelab_nonce) > 128:
        raise HTTPException(status_code=401, detail="bad nonce")
    if not _remember_nonce(x_homelab_nonce):
        raise HTTPException(status_code=401, detail="replayed nonce")
    if not x_homelab_client or len(x_homelab_client) > 80:
        raise HTTPException(status_code=401, detail="bad client id")

    body = await request.body()
    expected = sign(
        key,
        x_homelab_timestamp,
        x_homelab_nonce,
        request.method,
        request.url.path,
        body,
    )
    if not hmac.compare_digest(expected, x_homelab_signature.lower()):
        raise HTTPException(status_code=401, detail="bad signature")
    return x_homelab_client
