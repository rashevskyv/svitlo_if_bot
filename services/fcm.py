"""Firebase Cloud Messaging (HTTP v1) for the Android widget: one topic per (region, queue).
The service account key file path comes from FCM_KEY_FILE; without it nothing is sent."""
import base64
import json
import os
import re
import time

import aiohttp

_TOKEN = {"v": None, "exp": 0}
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"


def topic(region_id, queue_id) -> str:
    """Same name as Prefs.topicFor() in the Android app."""
    return re.sub(r"[^A-Za-z0-9\-_.~%]", "_", f"q_{region_id}_{queue_id}")


def _b64(b: bytes) -> bytes:
    return base64.urlsafe_b64encode(b).rstrip(b"=")


def _key():
    path = os.getenv("FCM_KEY_FILE", "")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def _token(session, key) -> str:
    """OAuth access token from a self-signed RS256 JWT (service account flow), cached for its lifetime."""
    if _TOKEN["exp"] > time.time() + 60:
        return _TOKEN["v"]
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    now = int(time.time())
    head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"iss": key["client_email"], "scope": _SCOPE, "aud": key["token_uri"],
                            "iat": now, "exp": now + 3600}).encode())
    pk = serialization.load_pem_private_key(key["private_key"].encode(), None)
    jwt = head + b"." + body
    jwt = (jwt + b"." + _b64(pk.sign(jwt, padding.PKCS1v15(), hashes.SHA256()))).decode()
    async with session.post(key["token_uri"], data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt}) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"FCM token {resp.status}: {(await resp.text())[:200]}")
        j = await resp.json()
    _TOKEN.update(v=j["access_token"], exp=now + int(j.get("expires_in", 3600)))
    return _TOKEN["v"]


async def send(region_id, queue_id, data: str) -> bool:
    """Send {"d": data} to the queue topic. False when FCM is not configured."""
    key = _key()
    if not key:
        return False
    msg = {"message": {"topic": topic(region_id, queue_id), "data": {"d": data},
                       "android": {"priority": "high", "ttl": "86400s", "collapse_key": "schedule"}}}  # offline phone gets only the latest
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        tok = await _token(s, key)
        async with s.post(f"https://fcm.googleapis.com/v1/projects/{key['project_id']}/messages:send",
                          json=msg, headers={"Authorization": f"Bearer {tok}"}) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"FCM send {resp.status}: {(await resp.text())[:200]}")
    return True
