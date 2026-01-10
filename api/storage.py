import os
from typing import Dict, Optional, Tuple

import requests

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_STORAGE_BUCKET = os.getenv("SUPABASE_STORAGE_BUCKET", "documents").strip()

if not SUPABASE_URL:
    raise RuntimeError("Missing SUPABASE_URL env var.")
if not SUPABASE_SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_SERVICE_ROLE_KEY env var.")

_HTTP = requests.Session()


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
    }
    if extra:
        headers.update(extra)
    return headers


def upload_bytes(storage_path: str, content: bytes, mime_type: str) -> None:
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{storage_path}"
    resp = _HTTP.put(
        url,
        headers=_headers({"Content-Type": mime_type}),
        data=content,
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase upload failed: {resp.status_code} {resp.text}")


def download_bytes(storage_path: str) -> bytes:
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_STORAGE_BUCKET}/{storage_path}"
    resp = _HTTP.get(url, headers=_headers(), timeout=60)
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase download failed: {resp.status_code} {resp.text}")
    return resp.content


def create_signed_url(storage_path: str, expires_in: int = 3600) -> str:
    url = f"{SUPABASE_URL}/storage/v1/object/sign/{SUPABASE_STORAGE_BUCKET}/{storage_path}"
    resp = _HTTP.post(
        url,
        headers=_headers({"Content-Type": "application/json"}),
        json={"expiresIn": expires_in},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Supabase signed URL failed: {resp.status_code} {resp.text}")
    data = resp.json()
    signed = data.get("signedURL") or data.get("signedUrl")
    if not signed:
        raise RuntimeError(f"Unexpected signed URL response: {data}")
    return f"{SUPABASE_URL}{signed}"


def storage_path_for_document(doc_id: str, filename: str) -> str:
    safe_name = filename.replace("\\", "_").replace("/", "_")
    return f"{doc_id}/{safe_name}"
