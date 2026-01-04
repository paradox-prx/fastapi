import os
import time
import json
import uuid
import logging
from typing import Dict, List, Optional, Any

import requests
from fastapi import FastAPI, UploadFile, File, Form, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ============================================================
# LOGGING (prints EVERYTHING useful)
# ============================================================
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("rag-demo")

# ============================================================
# CONFIG
# ============================================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY env var. Set it before running.")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_UPLOAD_BASE = "https://generativelanguage.googleapis.com/upload/v1beta"
MODEL_NAME = "gemini-2.5-flash"  # supports File Search :contentReference[oaicite:3]{index=3}

# In-memory sessions (OK for local; NOT reliable on serverless)
SESSIONS: Dict[str, Dict[str, Any]] = {}

# Reuse a session for connection pooling
HTTP = requests.Session()


# ============================================================
# SAFE LOG HELPERS
# ============================================================
def _redact_headers(h: Dict[str, str]) -> Dict[str, str]:
    out = dict(h)
    if "x-goog-api-key" in out:
        out["x-goog-api-key"] = "***REDACTED***"
    return out


def _pretty_json(x: Any) -> str:
    try:
        return json.dumps(x, indent=2, ensure_ascii=False)
    except Exception:
        return str(x)


def gemini_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, str]] = None,
    json_body: Optional[Dict[str, Any]] = None,
    data: Optional[bytes] = None,
    timeout: int = 60,
) -> requests.Response:
    """
    Wrapper that logs REQUEST + RESPONSE in detail.
    """
    headers = headers or {}
    params = params or {}

    # add api key both ways for robustness:
    # docs show ?key= works; API reference also shows x-goog-api-key header :contentReference[oaicite:4]{index=4}
    params = {**params, "key": GEMINI_API_KEY}
    headers = {**headers, "x-goog-api-key": GEMINI_API_KEY}

    log.debug("========== GEMINI HTTP REQUEST ==========")
    log.debug("%s %s", method.upper(), url)
    log.debug("Params: %s", _pretty_json(params))
    log.debug("Headers: %s", _pretty_json(_redact_headers(headers)))
    if json_body is not None:
        log.debug("JSON Body:\n%s", _pretty_json(json_body))
    if data is not None:
        log.debug("Binary body bytes: %s", len(data))

    resp = HTTP.request(
        method=method.upper(),
        url=url,
        headers=headers,
        params=params,
        json=json_body,
        data=data,
        timeout=timeout,
    )

    log.debug("========== GEMINI HTTP RESPONSE ==========")
    log.debug("Status: %s", resp.status_code)
    # show useful headers (not all)
    show_headers = {k: v for k, v in resp.headers.items() if k.lower() in {
        "content-type", "x-goog-upload-url", "x-request-id", "date"
    }}
    log.debug("Resp headers: %s", _pretty_json(show_headers))

    # print response body safely (truncate)
    text = resp.text or ""
    if len(text) > 4000:
        text = text[:4000] + "\n... (truncated) ..."
    log.debug("Resp body:\n%s", text)

    return resp


# ============================================================
# GEMINI: FILE SEARCH STORE + FILE UPLOAD + IMPORT
# ============================================================
def create_file_search_store(display_name: str) -> str:
    """
    POST /v1beta/fileSearchStores
    Returns store resource name like: fileSearchStores/...
    :contentReference[oaicite:5]{index=5}
    """
    url = f"{GEMINI_BASE}/fileSearchStores"
    resp = gemini_request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        json_body={"displayName": display_name},
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"create_file_search_store failed: {resp.status_code} {resp.text}")
    return resp.json()["name"]


def resumable_upload_file_to_files_api(file_bytes: bytes, mime_type: str, display_name: str) -> str:
    """
    Uses the documented resumable upload pattern to /upload/v1beta/files.
    Returns File.name like "files/abc123".
    :contentReference[oaicite:6]{index=6}
    """
    start_url = f"{GEMINI_UPLOAD_BASE}/files"

    start_headers = {
        "Content-Type": "application/json",
        "X-Goog-Upload-Protocol": "resumable",
        "X-Goog-Upload-Command": "start",
        "X-Goog-Upload-Header-Content-Length": str(len(file_bytes)),
        "X-Goog-Upload-Header-Content-Type": mime_type,
    }
    start_body = {"file": {"displayName": display_name}}

    start_resp = gemini_request(
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
        raise RuntimeError("Missing x-goog-upload-url in resumable start response headers")

    upload_headers = {
        "X-Goog-Upload-Offset": "0",
        "X-Goog-Upload-Command": "upload, finalize",
        "Content-Length": str(len(file_bytes)),
        "Content-Type": mime_type,
    }

    upload_resp = gemini_request(
        "POST",
        upload_url,
        headers=upload_headers,
        data=file_bytes,
        timeout=120,
    )
    if upload_resp.status_code >= 400:
        raise RuntimeError(f"files upload finalize failed: {upload_resp.status_code} {upload_resp.text}")

    j = upload_resp.json()
    # docs show top-level {"file": {...}} in examples :contentReference[oaicite:7]{index=7}
    file_obj = j.get("file", j)
    file_name = file_obj.get("name")
    if not file_name:
        raise RuntimeError(f"Upload response missing file name. Response: {j}")
    return file_name


def import_file_into_store(store_name: str, file_name: str) -> str:
    """
    POST /v1beta/{fileSearchStoreName}:importFile
    returns operation.name
    :contentReference[oaicite:8]{index=8}
    """
    url = f"{GEMINI_BASE}/{store_name}:importFile"
    resp = gemini_request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        json_body={"fileName": file_name},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"importFile failed: {resp.status_code} {resp.text}")
    op = resp.json()
    return op["name"]


def poll_operation(op_name: str, *, max_wait_s: int = 120, poll_every_s: int = 3) -> Dict[str, Any]:
    """
    GET /v1beta/{name=fileSearchStores/*/operations/*}
    :contentReference[oaicite:9]{index=9}
    """
    url = f"{GEMINI_BASE}/{op_name}"
    deadline = time.time() + max_wait_s
    last = None

    while time.time() < deadline:
        resp = gemini_request("GET", url, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"operations.get failed: {resp.status_code} {resp.text}")
        last = resp.json()
        if last.get("done") is True:
            return last
        time.sleep(poll_every_s)

    raise RuntimeError(f"Operation did not complete in {max_wait_s}s. Last: {last}")


def generate_with_file_search(store_name: str, system_prompt: str, user_message: str) -> str:
    """
    POST /v1beta/models/{model}:generateContent
    With File Search tool:
      "tools": [{"file_search": {"file_search_store_names":[store]}}]
    :contentReference[oaicite:10]{index=10}
    """
    url = f"{GEMINI_BASE}/models/{MODEL_NAME}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": [{"text": user_message}]}],
        "tools": [{"file_search": {"file_search_store_names": [store_name]}}],
    }

    resp = gemini_request(
        "POST",
        url,
        headers={"Content-Type": "application/json"},
        json_body=body,
        timeout=90,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"generateContent failed: {resp.status_code} {resp.text}")

    j = resp.json()
    try:
        return j["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        raise RuntimeError(f"Unexpected generateContent response shape: {j}")


# ============================================================
# FASTAPI APP
# ============================================================
app = FastAPI(title="Ephemeral RAG Demo (Verbose Logs)", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/favicon.ico")
def favicon():
    return JSONResponse({}, status_code=204)


# ============================================================
# SIMPLE FRONTEND (shows errors)
# ============================================================
@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>Ephemeral RAG Demo</title>
<style>
body { background:#0b0b0b; color:#fff; font-family:system-ui; }
.container { max-width:900px; margin:40px auto; }
input, textarea { width:100%; padding:10px; margin:6px 0; background:#111; color:#fff; border:1px solid #333; }
button { padding:12px 20px; background:#2563eb; color:#fff; border:none; cursor:pointer; }
.chat { border:1px solid #333; padding:15px; margin-top:20px; }
.msg { margin:8px 0; }
.user { color:#93c5fd; }
.bot { color:#a7f3d0; }
.err { color:#fca5a5; white-space:pre-wrap; margin-top:10px; }
.small { color:#aaa; font-size: 12px; }
</style>
</head>
<body>
<div class="container">

<h2>Ephemeral RAG Demo (Verbose Logs)</h2>
<div class="small">Tip: open your terminal to see full Gemini HTTP logs.</div>

<div id="setup">
  <input id="company" placeholder="Company name" value="Acme Inc."/>
  <input id="recipient" placeholder="Recipient name" value="Ali"/>
  <input id="persona" placeholder="Persona (e.g. VP Product)" value="VP Product, non-technical"/>
  <textarea id="summary" placeholder="High-level summary">This is a demo page. Answer using uploaded docs.</textarea>
  <input type="file" id="files" multiple/>
  <button onclick="startChat()">Start Chat</button>
  <div id="setupErr" class="err"></div>
</div>

<div id="chat" style="display:none;">
  <div class="chat" id="messages"></div>
  <input id="messageInput" placeholder="Ask a question..."/>
  <button onclick="sendMessage()">Send</button>
  <div id="chatErr" class="err"></div>
</div>

<script>
let sessionId = null;

async function startChat() {
  setupErr.textContent = "";
  const fd = new FormData();
  fd.append("company", company.value);
  fd.append("recipient", recipient.value);
  fd.append("persona", persona.value);
  fd.append("summary", summary.value);
  for (const f of files.files) fd.append("files", f);

  const res = await fetch("/api/setup", { method:"POST", body:fd });
  const data = await res.json();

  if (!res.ok) {
    setupErr.textContent = "Setup failed:\\n" + JSON.stringify(data, null, 2);
    return;
  }

  sessionId = data.session_id;
  setup.style.display = "none";
  chat.style.display = "block";
  messages.innerHTML += `<div class="msg bot">Bot: Ready. Ask me anything about the uploaded docs.</div>`;
}

async function sendMessage() {
  chatErr.textContent = "";
  if (!sessionId) {
    chatErr.textContent = "No session. Run setup first.";
    return;
  }

  const msg = messageInput.value.trim();
  if (!msg) return;

  messageInput.value = "";
  messages.innerHTML += `<div class="msg user">You: ${msg}</div>`;

  const res = await fetch("/api/chat", {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body:JSON.stringify({ session_id:sessionId, message:msg })
  });

  const data = await res.json();
  if (!res.ok) {
    chatErr.textContent = "Chat failed:\\n" + JSON.stringify(data, null, 2);
    return;
  }

  messages.innerHTML += `<div class="msg bot">Bot: ${data.answer}</div>`;
}
</script>

</div>
</body>
</html>
"""


# ============================================================
# API: SETUP
# ============================================================
@app.post("/api/setup")
async def setup(
    company: str = Form(...),
    recipient: str = Form(...),
    persona: str = Form(...),
    summary: str = Form(...),
    files: List[UploadFile] = File(...),
):
    """
    Creates a new File Search store, uploads & imports files, and stores session in memory.
    """
    try:
        if not files:
            return JSONResponse({"error": "No files uploaded"}, status_code=400)

        session_id = str(uuid.uuid4())
        store_display_name = f"demo-{session_id[:8]}"

        log.info("=== SETUP START session_id=%s store_display_name=%s ===", session_id, store_display_name)

        store_name = create_file_search_store(store_display_name)
        log.info("Created store: %s", store_name)

        imported = []
        for f in files:
            # read bytes
            b = await f.read()
            if not b:
                raise RuntimeError(f"Empty upload: {f.filename}")

            mime = f.content_type or "application/octet-stream"
            display_name = f.filename or "uploaded_file"

            log.info("Uploading file to Files API: filename=%s bytes=%d mime=%s", display_name, len(b), mime)
            file_name = resumable_upload_file_to_files_api(b, mime, display_name)
            log.info("Uploaded file_name=%s", file_name)

            log.info("Importing file into store: store=%s file=%s", store_name, file_name)
            op_name = import_file_into_store(store_name, file_name)
            log.info("Import operation: %s", op_name)

            op = poll_operation(op_name, max_wait_s=180, poll_every_s=3)
            if op.get("error"):
                raise RuntimeError(f"Import operation error: {op['error']}")
            imported.append({"filename": display_name, "file_name": file_name, "operation": op_name})

        system_prompt = f"""You are an AI assistant helping {recipient} from {company}.
Persona: {persona}

Page summary:
{summary}

Rules:
- Be concise and business-appropriate.
- If info is not in the docs, say you don't know.
"""

        SESSIONS[session_id] = {
            "store_name": store_name,
            "system_prompt": system_prompt,
            "created_at": time.time(),
        }

        log.info("=== SETUP DONE session_id=%s store_name=%s imported=%d ===", session_id, store_name, len(imported))

        return {
            "session_id": session_id,
            "store_name": store_name,
            "imported": imported,
        }

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log.error("SETUP FAILED: %s\n%s", str(e), err)
        return JSONResponse({"error": str(e), "trace": err}, status_code=500)


# ============================================================
# API: CHAT
# ============================================================
@app.post("/api/chat")
async def chat(payload: Dict[str, Any] = Body(...)):
    try:
        session_id = payload.get("session_id")
        message = (payload.get("message") or "").strip()

        if not session_id:
            return JSONResponse({"error": "Missing session_id"}, status_code=400)
        if not message:
            return JSONResponse({"error": "Missing message"}, status_code=400)

        session = SESSIONS.get(session_id)
        if not session:
            return JSONResponse({"error": "Session expired/no setup (in-memory store). Run setup again."}, status_code=400)

        answer = generate_with_file_search(
            session["store_name"],
            session["system_prompt"],
            message,
        )

        return {"answer": answer}

    except Exception as e:
        import traceback
        err = traceback.format_exc()
        log.error("CHAT FAILED: %s\n%s", str(e), err)
        return JSONResponse({"error": str(e), "trace": err}, status_code=500)
