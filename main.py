import os
import uuid
from typing import Dict, List

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import google.generativeai as genai

# -------------------------------------------------------------------
# CONFIG
# -------------------------------------------------------------------

genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

MODEL_NAME = "models/gemini-2.5-flash"

# In-memory demo session store (OK for v0)
SESSIONS: Dict[str, Dict] = {}

# -------------------------------------------------------------------
# APP
# -------------------------------------------------------------------

app = FastAPI(
    title="RAG Demo (Ephemeral)",
    version="0.0.1"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------------------------
# FRONTEND (SINGLE PAGE)
# -------------------------------------------------------------------

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
    textarea, input { width:100%; padding:10px; margin:6px 0; }
    button { padding:12px 20px; background:#2563eb; color:#fff; border:none; cursor:pointer; }
    .chat { border:1px solid #333; padding:15px; margin-top:20px; }
    .msg { margin:8px 0; }
    .user { color:#93c5fd; }
    .bot { color:#a7f3d0; }
  </style>
</head>
<body>
<div class="container">

<h2>RAG Demo (Ephemeral)</h2>

<div id="setup">
  <input id="company" placeholder="Company name"/>
  <input id="recipient" placeholder="Recipient name"/>
  <input id="persona" placeholder="Persona (e.g. VP Product, non-technical)"/>
  <textarea id="summary" placeholder="High-level summary for this page"></textarea>
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

# -------------------------------------------------------------------
# SETUP ENDPOINT
# -------------------------------------------------------------------

@app.post("/api/setup")
async def setup(
    company: str = Form(...),
    recipient: str = Form(...),
    persona: str = Form(...),
    summary: str = Form(...),
    files: List[UploadFile] = File(...)
):
    session_id = str(uuid.uuid4())
    store_name = f"demo-store-{session_id[:8]}"

    # Create file store (Gemini File Search)
    genai.files.create_file_store(name=store_name)

    for f in files:
        genai.files.upload(
            file_store=store_name,
            file=f.file,
            mime_type=f.content_type
        )

    system_prompt = f"""
You are an AI assistant helping {recipient} from {company}.
Persona: {persona}

This page summary:
{summary}

Answer clearly, concisely, and in a business-appropriate tone.
"""

    SESSIONS[session_id] = {
        "file_store": store_name,
        "system_prompt": system_prompt
    }

    return JSONResponse({
        "session_id": session_id
    })

# -------------------------------------------------------------------
# CHAT ENDPOINT
# -------------------------------------------------------------------

@app.post("/api/chat")
async def chat(payload: Dict):
    session_id = payload["session_id"]
    message = payload["message"]

    session = SESSIONS.get(session_id)
    if not session:
        return JSONResponse({"error": "Session expired"}, status_code=400)

    model = genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=session["system_prompt"],
        tools=[{
            "file_search": {
                "file_store_names": [session["file_store"]]
            }
        }]
    )

    response = model.generate_content(message)

    return JSONResponse({
        "answer": response.text
    })
