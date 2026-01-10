import time
from typing import Any, Dict, List, Optional

import requests

from . import db
from . import gemini_fs
from . import storage


def _log_event(job_id: str, message: str, level: str = "info", data: Optional[Dict[str, Any]] = None) -> None:
    db.execute(
        """
        INSERT INTO ingestion_job_events (job_id, level, message, data)
        VALUES (%s, %s, %s, %s)
        """,
        (job_id, level, message, data),
    )


def _set_job_status(job_id: str, status: str, error: Optional[str] = None, progress: Optional[int] = None) -> None:
    fields = ["status = %s", "updated_at = now()"]
    params: List[Any] = [status]
    if error is not None:
        fields.append("error = %s")
        params.append(error)
    if progress is not None:
        fields.append("progress = %s")
        params.append(progress)
    params.append(job_id)
    db.execute(f"UPDATE ingestion_jobs SET {', '.join(fields)} WHERE id = %s", params)


def _fetch_documents_for_job(job_id: str, offset: int, limit: int) -> List[Dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT d.*
        FROM ingestion_jobs j
        JOIN file_store_documents fsd ON fsd.file_store_id = j.file_store_id
        JOIN documents d ON d.id = fsd.document_id
        WHERE j.id = %s
        ORDER BY fsd.added_at ASC, d.id ASC
        OFFSET %s
        LIMIT %s
        """,
        (job_id, offset, limit),
    )


def _download_document_bytes(doc: Dict[str, Any]) -> bytes:
    if doc["source_type"] == "external_url":
        url = doc.get("external_url")
        if not url:
            raise RuntimeError("Document missing external_url")
        resp = requests.get(url, timeout=60)
        if resp.status_code >= 400:
            raise RuntimeError(f"External download failed: {resp.status_code} {resp.text}")
        return resp.content
    storage_path = doc.get("storage_path")
    if not storage_path:
        raise RuntimeError("Document missing storage_path")
    return storage.download_bytes(storage_path)


def run_ingestion_job(job_id: str, time_budget_s: int = 20, batch_size: int = 5) -> Dict[str, Any]:
    job = db.fetch_one("SELECT * FROM ingestion_jobs WHERE id = %s", (job_id,))
    if not job:
        raise RuntimeError("Job not found")

    if job["status"] in ("succeeded", "failed"):
        return job

    _set_job_status(job_id, "running")
    _log_event(job_id, "job_started")

    file_store = db.fetch_one("SELECT * FROM file_stores WHERE id = %s", (job["file_store_id"],))
    if not file_store:
        _set_job_status(job_id, "failed", error="File store not found")
        _log_event(job_id, "job_failed", level="error", data={"error": "File store not found"})
        return db.fetch_one("SELECT * FROM ingestion_jobs WHERE id = %s", (job_id,))

    _log_event(job_id, "gemini_store_ready", data={"gemini_store_name": file_store["gemini_store_name"]})

    progress = job.get("progress", 0) or 0
    total = job.get("total", 0) or 0
    if total == 0:
        total_row = db.fetch_one(
            """
            SELECT COUNT(*) AS cnt
            FROM file_store_documents
            WHERE file_store_id = %s
            """,
            (job["file_store_id"],),
        )
        total = total_row["cnt"]
        db.execute("UPDATE ingestion_jobs SET total = %s WHERE id = %s", (total, job_id))

    deadline = time.time() + time_budget_s
    processed = 0

    while time.time() < deadline and progress < total:
        docs = _fetch_documents_for_job(job_id, progress, min(batch_size, total - progress))
        if not docs:
            break
        for doc in docs:
            if time.time() >= deadline:
                break
            try:
                _log_event(job_id, "downloading_from_storage", data={"document_id": doc["id"]})
                content = _download_document_bytes(doc)
                _log_event(job_id, "uploading_to_gemini_files_api", data={"document_id": doc["id"]})
                file_name = gemini_fs.resumable_upload_file(
                    content,
                    doc["mime_type"],
                    doc.get("title") or doc.get("original_filename") or "document",
                )
                _log_event(job_id, "importing_into_file_search_store", data={"document_id": doc["id"]})
                op_name = gemini_fs.import_file_into_store(
                    file_store["gemini_store_name"],
                    file_name,
                    file_store.get("chunking_config"),
                )
                _log_event(job_id, "polling_operation", data={"document_id": doc["id"], "operation": op_name})
                op = gemini_fs.poll_operation(op_name, max_wait_s=120, poll_every_s=3)
                if op.get("error"):
                    raise RuntimeError(str(op["error"]))
                _log_event(job_id, "indexed_ok", data={"document_id": doc["id"], "file_name": file_name})
                progress += 1
                processed += 1
                _set_job_status(job_id, "running", progress=progress)
            except Exception as exc:
                _log_event(
                    job_id,
                    "indexed_failed",
                    level="error",
                    data={"document_id": doc["id"], "error": str(exc)},
                )
                _set_job_status(job_id, "failed", error=str(exc))
                _log_event(job_id, "job_failed", level="error", data={"error": str(exc)})
                return db.fetch_one("SELECT * FROM ingestion_jobs WHERE id = %s", (job_id,))

    if progress >= total:
        _set_job_status(job_id, "succeeded", progress=progress)
        _log_event(job_id, "job_succeeded", data={"processed": processed})
    else:
        _set_job_status(job_id, "running", progress=progress)

    return db.fetch_one("SELECT * FROM ingestion_jobs WHERE id = %s", (job_id,))
