import os
import time
from typing import Any, Dict, List, Optional

import requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()

if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY env var.")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_UPLOAD_BASE = "https://generativelanguage.googleapis.com/upload/v1beta"

_HTTP = requests.Session()


def _request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    data: Optional[bytes] = None,
    timeout: int = 60,
) -> requests.Response:
    headers = headers or {}
    params = params or {}
    params = {**params, "key": GEMINI_API_KEY}
    headers = {**headers, "x-goog-api-key": GEMINI_API_KEY}
    return _HTTP.request(
        method=method.upper(),
        url=url,
        headers=headers,
        params=params,
        json=json_body,
        data=data,
        timeout=timeout,
    )


def create_file_search_store(display_name: str) -> str:
    url = f"{GEMINI_BASE}/fileSearchStores"
    resp = _request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        json_body={"displayName": display_name},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"create_file_search_store failed: {resp.status_code} {resp.text}")
    return resp.json()["name"]


def resumable_upload_file(file_bytes: bytes, mime_type: str, display_name: str) -> str:
    start_url = f"{GEMINI_UPLOAD_BASE}/files"
    start_headers = {
        "Content-Type": "application/json",
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(len(file_bytes)),
        "X-Goog-Upload-Header-Content-Type": mime_type,
    }
    start_body = {"file": {"displayName": display_name}}

    start_resp = _request(
        "POST",
        start_url,
        headers=start_headers,
        json_body=start_body,
        timeout=30,
    )
    if start_resp.status_code >= 400:
        raise RuntimeError(f"files upload start failed: {start_resp.status_code} {start_resp.text}")

    upload_url = start_resp.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("Missing x-goog-upload-url in resumable upload response")

    upload_headers = {
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
        "Content-Length": str(len(file_bytes)),
        "Content-Type": mime_type,
    }

    upload_resp = _request(
        "POST",
        upload_url,
        headers=upload_headers,
        data=file_bytes,
        timeout=120,
    )
    if upload_resp.status_code >= 400:
        raise RuntimeError(f"files upload finalize failed: {upload_resp.status_code} {upload_resp.text}")

    j = upload_resp.json()
    file_obj = j.get("file", j)
    file_name = file_obj.get("name")
    if not file_name:
        raise RuntimeError(f"Upload response missing file name: {j}")
    return file_name


def import_file_into_store(store_name: str, file_name: str, chunking_config: Optional[Dict[str, Any]] = None) -> str:
    url = f"{GEMINI_BASE}/{store_name}:importFile"
    body: Dict[str, Any] = {"fileName": file_name}
    if chunking_config:
        body["chunkingConfig"] = chunking_config
    resp = _request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        json_body=body,
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"importFile failed: {resp.status_code} {resp.text}")
    return resp.json()["name"]


def poll_operation(op_name: str, max_wait_s: int = 180, poll_every_s: int = 3) -> Dict[str, Any]:
    url = f"{GEMINI_BASE}/{op_name}"
    deadline = time.time() + max_wait_s
    last = None
    while time.time() < deadline:
        resp = _request("GET", url, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"operations.get failed: {resp.status_code} {resp.text}")
        last = resp.json()
        if last.get("done") is True:
            return last
        time.sleep(poll_every_s)
    raise RuntimeError(f"Operation did not complete in {max_wait_s}s. Last: {last}")


def generate_content(system_prompt: str, user_message: str, store_names: List[str]) -> Dict[str, Any]:
    url = f"{GEMINI_BASE}/models/{GEMINI_MODEL}:generateContent"
    body: Dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
    }
    if store_names:
        body["tools"] = [{"file_search": {"file_search_store_names": store_names}}]
    resp = _request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        json_body=body,
        timeout=90,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"generateContent failed: {resp.status_code} {resp.text}")
    return resp.json()


def extract_answer_and_citations(response_json: Dict[str, Any]) -> Dict[str, Any]:
    answer = ""
    citations: List[Any] = []
    try:
        candidate = response_json["candidates"][0]
        parts = candidate["content"]["parts"]
        answer = "".join(p.get("text", "") for p in parts)
        citations = candidate.get("citationMetadata", {}).get("citations", [])
    except Exception:
        answer = ""
    return {"answer": answer, "citations": citations}
