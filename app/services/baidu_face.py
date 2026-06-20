import base64
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings

BAIDU_OAUTH_URL = "https://aip.baidubce.com/oauth/2.0/token"
BAIDU_BASE_URL = "https://aip.baidubce.com/rest/2.0/face/v3"

_access_token: str | None = None
_token_expire_at: float = 0


async def _get_access_token(client: httpx.AsyncClient) -> str:
    global _access_token, _token_expire_at

    if _access_token and time.time() < _token_expire_at - 60:
        return _access_token

    resp = await client.post(
        BAIDU_OAUTH_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.BAIDU_API_KEY,
            "client_secret": settings.BAIDU_SECRET_KEY,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    _access_token = data["access_token"]
    _token_expire_at = time.time() + data.get("expires_in", 2592000) - 60
    return _access_token


async def _call_baidu(client: httpx.AsyncClient, endpoint: str, body: dict) -> dict:
    token = await _get_access_token(client)
    url = f"{BAIDU_BASE_URL}{endpoint}?access_token={token}"
    resp = await client.post(url, json=body)
    data = resp.json()
    if data.get("error_code") and data["error_code"] != 0:
        raise RuntimeError(f"Baidu API error {data['error_code']}: {data.get('error_msg', 'unknown')}")
    return data


async def faceset_add_face(client: httpx.AsyncClient, group_id: str, image_base64: str, user_id: str, action_type: str = "APPEND") -> str:
    """Register a face to Baidu face library. Returns face_token."""
    data = await _call_baidu(
        client,
        "/faceset/user/add",
        {
            "image": image_base64,
            "image_type": "BASE64",
            "group_id": group_id,
            "user_id": str(user_id),
            "quality_control": "NORMAL",
            "liveness_control": "NONE",
            "action_type": action_type,
        },
    )
    return data["result"]["face_token"]


async def faceset_search(client: httpx.AsyncClient, group_id: str, image_base64: str) -> dict:
    """Search a face in the Baidu face library. Returns user_list with user_id and score."""
    data = await _call_baidu(
        client,
        "/search",
        {
            "image": image_base64,
            "image_type": "BASE64",
            "group_id_list": group_id,
            "quality_control": "LOW",
            "liveness_control": "NONE",
            "match_threshold": 80,
            "max_user_num": 1,
        },
    )
    return data["result"]


async def faceset_delete_face(client: httpx.AsyncClient, group_id: str, face_token: str) -> None:
    """Delete a face from Baidu face library."""
    await _call_baidu(
        client,
        "/faceset/face/delete",
        {
            "group_id": group_id,
            "face_token": face_token,
        },
    )


def read_image_as_base64(file_path: str) -> str:
    with open(file_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
