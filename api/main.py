import hashlib
import os
import secrets
import string
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from . import db
from . import gemini_fs
from . import jobs
from . import prompts
from . import storage

ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")

app = FastAPI(title="FastAPI Gemini File Search", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def require_admin(x_admin_key: str = Header(..., alias="X-Admin-Key")) -> None:
    if not ADMIN_API_KEY:
        raise HTTPException(status_code=500, detail="Missing ADMIN_API_KEY env var.")
    if x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _generate_slug(length: int = 20) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _page_by_slug(slug: str) -> Dict[str, Any]:
    page = db.fetch_one(
        """
        SELECT p.*, r.name AS recipient_name, r.company_name, r.persona
        FROM pages p
        JOIN recipients r ON r.id = p.recipient_id
        WHERE p.slug = %s
        """,
        (slug,),
    )
    if not page or not page["is_active"]:
        raise HTTPException(status_code=404, detail="Page not found")
    return page


def _display_documents_for_page(page_id: str) -> List[Dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT pd.document_id, pd.display_title, pd.display_caption, pd.sort_order,
               d.storage_path, d.external_url, d.source_type
        FROM page_documents pd
        JOIN documents d ON d.id = pd.document_id
        WHERE pd.page_id = %s
        ORDER BY pd.sort_order ASC, pd.document_id ASC
        """,
        (page_id,),
    )


def _file_store_names_for_page(page_id: str) -> List[str]:
    rows = db.fetch_all(
        """
        SELECT fs.gemini_store_name
        FROM page_file_stores pfs
        JOIN file_stores fs ON fs.id = pfs.file_store_id
        WHERE pfs.page_id = %s
        ORDER BY fs.name ASC
        """,
        (page_id,),
    )
    return [r["gemini_store_name"] for r in rows]


def _signed_url_for_doc(doc: Dict[str, Any]) -> Optional[str]:
    if doc["source_type"] == "external_url":
        return doc.get("external_url")
    if doc.get("storage_path"):
        return storage.create_signed_url(doc["storage_path"])
    return None


@app.get("/v1/pages/{slug}")
def get_page(slug: str):
    page = _page_by_slug(slug)
    docs = _display_documents_for_page(page["id"])
    display_docs = []
    for doc in docs:
        display_docs.append(
            {
                "document_id": doc["document_id"],
                "display_title": doc["display_title"],
                "display_caption": doc["display_caption"],
                "download_url": _signed_url_for_doc(doc),
            }
        )
    return {
        "title": page["title"],
        "template_key": page["template_key"],
        "recipient": {
            "name": page["recipient_name"],
            "company_name": page["company_name"],
            "persona": page["persona"],
        },
        "summary_markdown": page["summary_markdown"],
        "details_markdown": page["details_markdown"],
        "documents": display_docs,
    }


@app.get("/p/{slug}")
def public_page_proxy(slug: str):
    return get_page(slug)


@app.post("/v1/pages/{slug}/events/open")
def page_open(slug: str):
    page = _page_by_slug(slug)
    db.execute(
        """
        INSERT INTO analytics_events (page_id, event_type, payload)
        VALUES (%s, 'open', %s)
        """,
        (page["id"], {}),
    )
    return {"ok": True}


@app.post("/v1/pages/{slug}/events/click")
def page_click(slug: str, payload: Dict[str, Any] = Body(...)):
    page = _page_by_slug(slug)
    db.execute(
        """
        INSERT INTO analytics_events (page_id, event_type, payload)
        VALUES (%s, 'click', %s)
        """,
        (page["id"], payload),
    )
    return {"ok": True}


@app.post("/v1/pages/{slug}/chat/sessions")
def create_chat_session(slug: str, request: Request):
    page = _page_by_slug(slug)
    ip = request.client.host if request.client else ""
    ip_hash = hashlib.sha256(ip.encode("utf-8")).hexdigest() if ip else None
    user_agent = request.headers.get("user-agent")
    row = db.execute_returning(
        """
        INSERT INTO chat_sessions (page_id, last_seen_at, ip_hash, user_agent)
        VALUES (%s, now(), %s, %s)
        RETURNING id
        """,
        (page["id"], ip_hash, user_agent),
    )
    return {"session_id": row["id"]}


@app.post("/v1/chat/{session_id}/messages")
def post_chat_message(session_id: str, payload: Dict[str, Any] = Body(...)):
    message = (payload.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing message")

    session = db.fetch_one("SELECT * FROM chat_sessions WHERE id = %s", (session_id,))
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    page = db.fetch_one(
        """
        SELECT p.*, r.name AS recipient_name, r.company_name, r.persona
        FROM pages p
        JOIN recipients r ON r.id = p.recipient_id
        WHERE p.id = %s
        """,
        (session["page_id"],),
    )
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    display_docs = _display_documents_for_page(page["id"])
    documents_display_list = prompts.render_documents_list(display_docs)
    context = {
        "page.title": page["title"],
        "page.summary_markdown": page.get("summary_markdown") or "",
        "page.details_markdown": page.get("details_markdown") or "",
        "recipient.name": page["recipient_name"],
        "recipient.company_name": page["company_name"],
        "recipient.persona": page.get("persona") or "",
        "documents_display_list": documents_display_list,
    }
    system_prompt = prompts.render_prompt(page["system_prompt_template"], context)
    store_names = _file_store_names_for_page(page["id"])

    resp = gemini_fs.generate_content(system_prompt, message, store_names)
    result = gemini_fs.extract_answer_and_citations(resp)
    answer = result["answer"]
    citations = result["citations"]

    db.execute(
        """
        INSERT INTO chat_messages (session_id, role, content, model, citations)
        VALUES (%s, 'user', %s, %s, %s), (%s, 'assistant', %s, %s, %s)
        """,
        (
            session_id,
            message,
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            None,
            session_id,
            answer,
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            citations,
        ),
    )

    return {"answer": answer, "citations": citations}


@app.get("/v1/chat/{session_id}/messages")
def get_chat_messages(session_id: str):
    rows = db.fetch_all(
        """
        SELECT role, content, model, citations, created_at
        FROM chat_messages
        WHERE session_id = %s
        ORDER BY created_at ASC
        """,
        (session_id,),
    )
    return {"messages": rows}


@app.post("/v1/admin/recipients", dependencies=[Depends(require_admin)])
def create_recipient(payload: Dict[str, Any] = Body(...)):
    row = db.execute_returning(
        """
        INSERT INTO recipients (name, email, company_name, persona)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (
            payload["name"],
            payload["email"],
            payload["company_name"],
            payload.get("persona"),
        ),
    )
    return {"id": row["id"]}


@app.post("/v1/admin/documents", dependencies=[Depends(require_admin)])
async def create_document(
    request: Request,
    file: Optional[UploadFile] = File(None),
    title: Optional[str] = Form(None),
    internal_description: Optional[str] = Form(None),
    external_url: Optional[str] = Form(None),
    mime_type: Optional[str] = Form(None),
):
    data: Dict[str, Any] = {}
    if request.headers.get("content-type", "").startswith("application/json"):
        data = await request.json()
        title = data.get("title")
        internal_description = data.get("internal_description")
        external_url = data.get("external_url")
        mime_type = data.get("mime_type")

    if not title:
        raise HTTPException(status_code=400, detail="Missing title")

    doc_id = str(uuid.uuid4())
    source_type = "storage"
    storage_path = None
    original_filename = None
    sha256 = None
    size_bytes = None

    if external_url:
        source_type = "external_url"
        mime_type = mime_type or "application/octet-stream"
    elif file is not None:
        content = await file.read()
        original_filename = file.filename
        mime_type = file.content_type or mime_type or "application/octet-stream"
        sha256 = hashlib.sha256(content).hexdigest()
        size_bytes = len(content)
        storage_path = storage.storage_path_for_document(str(doc_id), original_filename or "document")
        storage.upload_bytes(storage_path, content, mime_type)
    else:
        raise HTTPException(status_code=400, detail="Provide a file or external_url")

    db.execute(
        """
        INSERT INTO documents (
            id, source_type, mime_type, original_filename, storage_bucket, storage_path,
            external_url, title, internal_description, sha256, size_bytes
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            doc_id,
            source_type,
            mime_type,
            original_filename,
            storage.SUPABASE_STORAGE_BUCKET,
            storage_path,
            external_url,
            title,
            internal_description,
            sha256,
            size_bytes,
        ),
    )
    return {"id": doc_id, "storage_path": storage_path}


@app.post("/v1/admin/file-stores", dependencies=[Depends(require_admin)])
def create_file_store(payload: Dict[str, Any] = Body(...)):
    store_name = gemini_fs.create_file_search_store(payload["name"])
    row = db.execute_returning(
        """
        INSERT INTO file_stores (name, description, gemini_store_name, chunking_config)
        VALUES (%s, %s, %s, %s)
        RETURNING id, gemini_store_name
        """,
        (
            payload["name"],
            payload.get("description"),
            store_name,
            payload.get("chunking_config"),
        ),
    )
    return {"id": row["id"], "gemini_store_name": row["gemini_store_name"]}


@app.post("/v1/admin/file-stores/{file_store_id}/documents", dependencies=[Depends(require_admin)])
def attach_documents(file_store_id: str, payload: Dict[str, Any] = Body(...)):
    document_ids = payload.get("document_ids") or []
    if not document_ids:
        raise HTTPException(status_code=400, detail="Missing document_ids")
    for doc_id in document_ids:
        db.execute(
            """
            INSERT INTO file_store_documents (file_store_id, document_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (file_store_id, doc_id),
        )
    job_id = None
    if payload.get("create_ingestion_job"):
        total_row = db.fetch_one(
            "SELECT COUNT(*) AS cnt FROM file_store_documents WHERE file_store_id = %s",
            (file_store_id,),
        )
        row = db.execute_returning(
            """
            INSERT INTO ingestion_jobs (job_type, status, file_store_id, total)
            VALUES ('index_file_store', 'queued', %s, %s)
            RETURNING id
            """,
            (file_store_id, total_row["cnt"]),
        )
        job_id = row["id"]
    return {"ok": True, "job_id": job_id}


@app.post("/v1/admin/pages", dependencies=[Depends(require_admin)])
def create_page(payload: Dict[str, Any] = Body(...)):
    slug = payload.get("slug") or _generate_slug()
    row = db.execute_returning(
        """
        INSERT INTO pages (slug, title, recipient_id, template_key, system_prompt_template,
                           summary_markdown, details_markdown)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, slug
        """,
        (
            slug,
            payload["title"],
            payload["recipient_id"],
            payload["template_key"],
            payload["system_prompt_template"],
            payload.get("summary_markdown"),
            payload.get("details_markdown"),
        ),
    )

    for fs_id in payload.get("file_store_ids", []):
        db.execute(
            """
            INSERT INTO page_file_stores (page_id, file_store_id)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (row["id"], fs_id),
        )

    for doc in payload.get("display_documents", []):
        db.execute(
            """
            INSERT INTO page_documents (page_id, document_id, display_title, display_caption, sort_order)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                row["id"],
                doc["document_id"],
                doc["display_title"],
                doc["display_caption"],
                doc.get("sort_order", 0),
            ),
        )

    public_url = f"{PUBLIC_BASE_URL}/p/{row['slug']}" if PUBLIC_BASE_URL else f"/p/{row['slug']}"
    return {"id": row["id"], "slug": row["slug"], "public_url": public_url}


@app.post("/v1/admin/pages/{page_id}/takedown", dependencies=[Depends(require_admin)])
def takedown_page(page_id: str):
    db.execute(
        """
        UPDATE pages
        SET is_active = false, takedown_at = now()
        WHERE id = %s
        """,
        (page_id,),
    )
    return {"ok": True}


@app.post("/v1/admin/ingestion-jobs", dependencies=[Depends(require_admin)])
def create_ingestion_job(payload: Dict[str, Any] = Body(...)):
    file_store_id = payload.get("file_store_id")
    if not file_store_id:
        raise HTTPException(status_code=400, detail="Missing file_store_id")
    total_row = db.fetch_one(
        "SELECT COUNT(*) AS cnt FROM file_store_documents WHERE file_store_id = %s",
        (file_store_id,),
    )
    row = db.execute_returning(
        """
        INSERT INTO ingestion_jobs (job_type, status, file_store_id, total)
        VALUES ('index_file_store', 'queued', %s, %s)
        RETURNING id
        """,
        (file_store_id, total_row["cnt"]),
    )
    return {"id": row["id"]}


@app.post("/v1/admin/ingestion-jobs/{job_id}/run", dependencies=[Depends(require_admin)])
def run_ingestion_job(job_id: str, payload: Dict[str, Any] = Body(default={})):
    time_budget_s = int(payload.get("time_budget_s", 20))
    batch_size = int(payload.get("batch_size", 5))
    job = jobs.run_ingestion_job(job_id, time_budget_s=time_budget_s, batch_size=batch_size)
    return job


@app.get("/v1/admin/ingestion-jobs/{job_id}", dependencies=[Depends(require_admin)])
def get_ingestion_job(job_id: str):
    row = db.fetch_one("SELECT * FROM ingestion_jobs WHERE id = %s", (job_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return row


@app.get("/v1/admin/ingestion-jobs/{job_id}/events", dependencies=[Depends(require_admin)])
def get_ingestion_job_events(job_id: str, after_id: int = 0):
    rows = db.fetch_all(
        """
        SELECT id, ts, level, message, data
        FROM ingestion_job_events
        WHERE job_id = %s AND id > %s
        ORDER BY id ASC
        """,
        (job_id, after_id),
    )
    return {"events": rows}
