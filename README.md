# FastAPI RAG Portal (Supabase + Gemini File Search)

This repo contains a FastAPI backend that serves both JSON APIs and HTML admin/public pages. Public pages are available by secret slug at `/p/{slug}` and include a chat UI grounded in attached file stores.

## Architecture overview

- **FastAPI app**: `api/main.py`
- **DB access**: `api/db.py` (psycopg2 pool, autocommit)
- **Supabase Storage**: `api/storage.py` (upload, download, signed URLs)
- **Gemini File Search**: `api/gemini_fs.py` (REST, resumable upload + import + generate)
- **Ingestion jobs**: `api/jobs.py` (time-boxed ingestion with events)
- **HTML UI**: `templates/*.html` + `static/*.js` + `static/app.css`
- **Schema migration**: `migrations/001_init.sql`

## Requirements

Python 3.11+ recommended.

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run locally:

```bash
uvicorn api.main:app --reload
```

## Environment variables

Create `.env` (see `.env.example`):

- `SUPABASE_DB_DSN`  
  Example: `postgresql://USER:PASSWORD@HOST:6543/postgres?sslmode=require`
- `SUPABASE_URL`  
  Example: `https://your-project.supabase.co`
- `SUPABASE_SERVICE_ROLE_KEY`  
  **Service role key** (server-only). Required for storage uploads.
- `SUPABASE_STORAGE_BUCKET`  
  Default: `documents`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`  
  Default: `gemini-2.5-flash`
- `ADMIN_API_KEY`
- `PUBLIC_BASE_URL`  
  Used to build public URLs in responses.

## Database schema

All tables use UUID primary keys. Full SQL is in `migrations/001_init.sql`.

Core tables:

- `recipients`  
  `id`, `name`, `email`, `company_name`, `persona`, `created_at`
- `documents`  
  `id`, `source_type`, `file_type`, `mime_type`, `original_filename`, `storage_bucket`, `storage_path`, `external_url`, `title`, `internal_description`, `sha256`, `size_bytes`, `created_at`
- `file_stores`  
  `id`, `name`, `description`, `gemini_store_name`, `chunking_config`, `created_at`
- `pages`  
  `id`, `slug`, `title`, `recipient_id`, `template_key`, `system_prompt_template`, `summary_markdown`, `details_markdown`, `is_active`, `takedown_at`, `created_at`
- `chat_sessions`, `chat_messages`
- `analytics_events`
- `ingestion_jobs`, `ingestion_job_events`

Bridges:

- `file_store_documents` (many-to-many)
- `page_file_stores` (many-to-many)
- `page_documents` (display config for public pages)

Indexes:

- `pages(slug)`
- `documents(storage_path)`
- `analytics_events(page_id, created_at)`
- `chat_messages(session_id, created_at)`
- `ingestion_job_events(job_id, id)`

## Storage

- Uploads go to Supabase Storage bucket `SUPABASE_STORAGE_BUCKET`.
- `storage_path` is saved as a **relative path** for later reindexing.
- Public pages return **signed download URLs** (bucket remains private).
- Filenames are sanitized and URL-encoded to avoid invalid keys.

## Gemini File Search

Flow:

1. Upload file bytes to Gemini File API (resumable).
2. `importFile` into a File Search Store.
3. Poll operation until done.
4. Use File Search stores during `generateContent`.

Implementation: `api/gemini_fs.py`

## Ingestion jobs

Ingestion is time-boxed for serverless environments:

- `POST /v1/admin/ingestion-jobs` creates a job.
- `POST /v1/admin/ingestion-jobs/{job_id}/run` processes a batch.
- Events are recorded in `ingestion_job_events` with standard message strings.
- UI polls job status and events.

Standard event messages:

- `job_started`, `gemini_store_ready`
- `downloading_from_storage`, `uploading_to_gemini_files_api`
- `importing_into_file_search_store`, `polling_operation`
- `indexed_ok`, `indexed_failed`
- `job_succeeded`, `job_failed`

## Prompt templating

`system_prompt_template` supports placeholder replacements:

- `{{page.title}}`
- `{{page.summary_markdown}}`
- `{{page.details_markdown}}`
- `{{recipient.name}}`
- `{{recipient.company_name}}`
- `{{recipient.persona}}`
- `{{documents_display_list}}`

The backend builds a rendered prompt at chat time.

## HTML pages

Public:

- `GET /` landing
- `GET /p/{slug}` public page (dynamic)

Admin:

- `GET /admin`
- `GET /admin/documents`
- `GET /admin/file-stores`
- `GET /admin/pages`
- `GET /admin/jobs`
- `GET /admin/analytics`

Admin pages store `ADMIN_KEY` in `localStorage` and send `X-Admin-Key` on all admin API calls.

## JSON API

Public:

- `GET /v1/pages/{slug}`
- `POST /v1/pages/{slug}/events/open`
- `POST /v1/pages/{slug}/events/click`
- `POST /v1/pages/{slug}/chat/sessions`
- `POST /v1/chat/{session_id}/messages`
- `GET /v1/chat/{session_id}/messages`

Admin (requires `X-Admin-Key`):

- `POST /v1/admin/recipients`
- `GET /v1/admin/recipients`
- `POST /v1/admin/documents`
- `GET /v1/admin/documents`
- `POST /v1/admin/file-stores`
- `GET /v1/admin/file-stores`
- `POST /v1/admin/file-stores/{file_store_id}/documents`
- `POST /v1/admin/pages`
- `GET /v1/admin/pages`
- `POST /v1/admin/pages/{page_id}/takedown`
- `POST /v1/admin/ingestion-jobs`
- `GET /v1/admin/ingestion-jobs`
- `GET /v1/admin/ingestion-jobs/{job_id}`
- `GET /v1/admin/ingestion-jobs/{job_id}/events`
- `POST /v1/admin/ingestion-jobs/{job_id}/run`
- `GET /v1/admin/analytics/summary?days=30`

## Notes

- Slugs should be high entropy. You can omit `slug` in page creation to auto-generate.
- Only `.pdf` and `.md` documents are accepted.
- Supabase Storage must have the target bucket created in advance.
- Use the service role key for storage uploads to avoid RLS errors.
