import os
import uuid
import requests
from typing import Dict, List

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# =========================================================
# CONFIG
# =========================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY not set")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
MODEL_NAME = "models/gemini-2.5-flash"

# Ephemeral in-memory session store
SESSIONS: Dict[str, Dict] = {}

# =========================================================
# GEMINI FILE SEARCH (REST HELPERS)
# =========================================================

def gemini_headers():
    return {
        "Authorization": f"Bearer {GEMINI_API_KEY}",
        "Content-Type": "application/json",
    }


def create_file_store(display_name: str) -> str:
    """
    Creates a Gemini File Search store.
    Returns the store resource name.
    """
    res = requests.post(
        f"{GEMINI_BASE}/fileSearchStores",
        headers=gemini_headers(),
        json={"displayName": display_name},
        timeout=30,
    )
    res.raise_for_status()
    return res.json()["name"]


def upload_file_to_store(store_id: str, file: UploadFile):
    """
    Uploads a file and attaches it to a file store.
    """
    data = file.file.read()

    # 1) Upload file bytes
    upload_res = requests.post(
        f"{GEMINI_BASE}/files:upload",
        headers={
            "Authorization": f"Bearer {GEMINI_API_KEY}",
            "X-Goog-Upload-Protocol": "raw",
            "X-Goog-Upload-File-Name": file.filename,
            "Content-Type": file.content_type or "application/octet-stream",
        },
        data=data,
        timeout=60,
    )
    upload_res.raise_for_status()
    file_name = upload_res.json()["name"]

    # 2) Attach file to store
    attach_res = requests.post(
        f"{GEMINI_BASE}/{store_id}:addFile",
        headers=gemini_headers(),
        json={"file": file_name},
        timeout=30,
    )
    attach_res.raise_for_status()


def gemini_chat(system_prompt: str, store_id: str, user_message: str) -> str:
    """
    Sends a chat request to Gemini using File Search.
    """
    payload = {
        "model": MODEL_NAME,
        "systemInstruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_message}],
            }
        ],
        "tools": [
            {
                "file_search": {
                    "file_store_names": [store_id]
                }
            }
        ],
    }

    res = requests.post(
        f"{GEMINI_BASE}/models/gemini-2.5-flash:generateContent",
        headers=gemini_headers(),
        json=payload,
        timeout=60,
    )
    res.raise_for_status()

    return res.json()["candidates"][0]["content"]["parts"][0]["text"]

# =========================================================
# FASTAPI APP
# =========================================================

app = FastAPI(title="Ephemeral RAG Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# FRONTEND (SINGLE PAGE)
# =========================================================

@app.get("/", response_class=HTMLResponse)
def index():
    return """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8"/>
<title>RAG Demo</title>
<style>
body { background:#0b0b0b; color:#fff; font-family:system-ui; }
.container { max-width:900px; margin:40px auto; }
input, textarea { width:100%; padding:10px; margin:6px 0; background:#111; color:#fff; border:1px solid #333; }
button { padding:12px 20px; background:#2563eb; color:#fff; border:none; cursor:pointer; }
.chat { border:1px solid #333; padding:15px; margin-top:20px; }
.msg { margin:8px 0; }
.user { color:#93c5fd; }
.bot { color:#a7f3d0; }
</style>
</head>
<body>
<div class="container">

<h2>Ephemeral RAG Demo</h2>

<div id="setup">
  <input id="company" placeholder="Company name"/>
  <input id="recipient" placeholder="Recipient name"/>
  <input id="persona" placeholder="Persona (e.g. VP Product, non-technical)"/>
  <textarea id="summary" placeholder="High-level summary"></textarea>
  <input type="file" id="files" multiple/>
  <button onclick="startChat()">Start Chat</button>
</div>

<div id="chat" style="display:none;">
  <div class="chat" id="messages"></div>
  <input id="messageInput" placeholder="Ask a question..."/>
  <button onclick="sendMessage()">Send</button>
</div>

<script>
let sessionId = null;

async function startChat() {
  const fd = new FormData();
  fd.append("company", company.value);
  fd.append("recipient", recipient.value);
  fd.append("persona", persona.value);
  fd.append("summary", summary.value);
  for (const f of files.files) fd.append("files", f);

  const res = await fetch("/api/setup", { method:"POST", body:fd });
  const data = await res.json();

  sessionId = data.session_id;
  setup.style.display = "none";
  chat.style.display = "block";
}

async function sendMessage() {
  const msg = messageInput.value;
  messageInput.value = "";
  messages.innerHTML += `<div class="msg user">You: ${msg}</div>`;

  const res = await fetch("/api/chat", {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body:JSON.stringify({ session_id:sessionId, message:msg })
  });
  const data = await res.json();

  messages.innerHTML += `<div class="msg bot">Bot: ${data.answer}</div>`;
}
</script>

</div>
</body>
</html>
"""

# =========================================================
# SETUP ENDPOINT
# =========================================================

@app.post("/api/setup")
async def setup(
    company: str = Form(...),
    recipient: str = Form(...),
    persona: str = Form(...),
    summary: str = Form(...),
    files: List[UploadFile] = File(...)
):
    session_id = str(uuid.uuid4())
    store_display_name = f"demo-{session_id[:6]}"

    store_id = create_file_store(store_display_name)

    for f in files:
        upload_file_to_store(store_id, f)

    system_prompt = f"""
You are an AI assistant helping {recipient} from {company}.
Persona: {persona}

Page summary:
{summary}

Answer clearly, concisely, and in a professional business tone.
"""

    SESSIONS[session_id] = {
        "store_id": store_id,
        "system_prompt": system_prompt,
    }

    return {"session_id": session_id}

# =========================================================
# CHAT ENDPOINT
# =========================================================

@app.post("/api/chat")
async def chat(payload: Dict):
    session = SESSIONS.get(payload.get("session_id"))
    if not session:
        return JSONResponse({"error": "Session expired"}, status_code=400)

    answer = gemini_chat(
        system_prompt=session["system_prompt"],
        store_id=session["store_id"],
        user_message=payload["message"],
    )

    return {"answer": answer}
