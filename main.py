import os
import uuid
import requests
from typing import Dict, List

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ============================================================
# CONFIG
# ============================================================

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY must be set in env vars")

# Base for Gemini REST endpoints
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Model we will call for generation
MODEL_NAME = "gemini-2.5-flash"

# In-memory session store (for ephemeral demo)
SESSIONS: Dict[str, Dict] = {}

# ============================================================
# HELPER FUNCTIONS (REST Gemini File Search)
# ============================================================

def gemini_headers_json():
    """
    Headers for JSON endpoints with the API key.
    """
    return {
        "x-goog-api-key": GEMINI_API_KEY,
        "Content-Type": "application/json"
    }


def create_file_search_store(display_name: str) -> str:
    """
    Creates a File Search store and returns the store resource name.
    """
    url = f"{GEMINI_BASE}/fileSearchStores"
    payload = {"displayName": display_name}

    res = requests.post(url, headers=gemini_headers_json(), json=payload, timeout=30)
    res.raise_for_status()
    data = res.json()
    return data["name"]  # This is e.g. "fileSearchStores/123abc…"


def upload_and_attach_file(store_id: str, file: UploadFile):
    """
    Upload the raw file bytes to Gemini and attach it to the given store.
    """
    # Read full file bytes
    content = file.file.read()

    # First: upload the file to Gemini
    upload_headers = {
        "x-goog-api-key": GEMINI_API_KEY,
        "X-Goog-Upload-Protocol": "raw",
        "X-Goog-Upload-File-Name": file.filename,
        "Content-Type": file.content_type or "application/octet-stream"
    }

    upload_url = f"{GEMINI_BASE}/files:upload"
    upload_response = requests.post(upload_url, headers=upload_headers, data=content, timeout=60)
    upload_response.raise_for_status()
    uploaded_name = upload_response.json()["name"]  # The internal file name

    # Second: attach the uploaded file to the store
    attach_url = f"{GEMINI_BASE}/{store_id}:addFile"
    attach_payload = {"file": uploaded_name}

    attached_response = requests.post(attach_url, headers=gemini_headers_json(), json=attach_payload, timeout=30)
    attached_response.raise_for_status()
    return attached_response.json()


def generate_with_file_search(store_id: str, system_prompt: str, user_message: str) -> str:
    """
    Generate a Gemini response grounded with RAG using File Search.
    """
    gen_url = f"{GEMINI_BASE}/models/{MODEL_NAME}:generateContent"

    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_message}]
            }
        ],
        "tools": [
            {
                "file_search": {
                    "file_search_store_names": [store_id]
                }
            }
        ]
    }

    res = requests.post(gen_url, headers=gemini_headers_json(), json=body, timeout=70)
    res.raise_for_status()
    j = res.json()
    return j["candidates"][0]["content"]["parts"][0]["text"]


# ============================================================
# FASTAPI SETUP
# ============================================================

app = FastAPI(title="Ephemeral RAG Demo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# FRONTEND HTML
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
</style>
</head>
<body>
<div class="container">

<h2>Ephemeral RAG Demo</h2>

<div id="setup">
  <input id="company" placeholder="Company name"/>
  <input id="recipient" placeholder="Recipient name"/>
  <input id="persona" placeholder="Persona (e.g. VP Product)"/>
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

  document.getElementById("setup").style.display = "none";
  document.getElementById("chat").style.display = "block";
}

async function sendMessage() {
  const msg = document.getElementById("messageInput").value;
  document.getElementById("messageInput").value = "";
  document.getElementById("messages").innerHTML += `<div class="msg user">You: ${msg}</div>`;

  const res = await fetch("/api/chat", {
    method:"POST",
    headers:{ "Content-Type":"application/json" },
    body:JSON.stringify({ session_id:sessionId, message:msg })
  });
  const d = await res.json();
  document.getElementById("messages").innerHTML += `<div class="msg bot">Bot: ${d.answer}</div>`;
}
</script>

</div>
</body>
</html>
"""

# ============================================================
# SETUP ENDPOINT
# ============================================================

@app.post("/api/setup")
async def setup(
    company: str = Form(...),
    recipient: str = Form(...),
    persona: str = Form(...),
    summary: str = Form(...),
    files: List[UploadFile] = File(...)
):
    # Create a unique session
    session_id = str(uuid.uuid4())
    store_display_name = f"demo-{session_id[:8]}"

    # Create the File Search store
    try:
        store_id = create_file_search_store(store_display_name)
    except Exception as e:
        return JSONResponse({"error": f"Failed to create store: {str(e)}"}, status_code=500)

    # Upload & attach each document
    for f in files:
        try:
            upload_and_attach_file(store_id, f)
        except Exception as e:
            return JSONResponse({"error": f"Upload failed: {str(e)}"}, status_code=500)

    system_prompt = f"""
You are an AI assistant helping {recipient} from {company}.
Persona: {persona}

Page summary:
{summary}
"""

    # Save in memory
    SESSIONS[session_id] = {
        "store_id": store_id,
        "system_prompt": system_prompt
    }

    return {"session_id": session_id}


# ============================================================
# CHAT ENDPOINT
# ============================================================

@app.post("/api/chat")
async def chat(payload: Dict):
    session_id = payload.get("session_id")
    message = payload.get("message")

    session = SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"error":"Session expired"}, status_code=400)

    try:
        answer = generate_with_file_search(
            session["store_id"],
            session["system_prompt"],
            message
        )
    except Exception as e:
        return JSONResponse({"error":str(e)}, status_code=500)

    return {"answer": answer}
