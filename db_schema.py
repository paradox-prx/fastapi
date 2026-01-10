import os
from dotenv import load_dotenv
import psycopg2

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise SystemExit("Missing DATABASE_URL env var. Put it in .env or your shell.")

# ---- IMPORTANT for Supabase Transaction Pooler (port 6543) ----
# Supabase transaction mode does not support prepared statements. :contentReference[oaicite:4]{index=4}
# psycopg2 does not use psycopg3's prepare_threshold behavior.

DDL = r"""
-- Extensions (UUID generation)
-- gen_random_uuid() is provided by pgcrypto; enable it if you use it for UUID defaults.
-- Supabase supports enabling extensions, including uuid-ossp and pgcrypto. :contentReference[oaicite:6]{index=6}
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------- ENUM TYPES ----------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_source_type') THEN
    CREATE TYPE document_source_type AS ENUM ('storage', 'external_url');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_file_type') THEN
    CREATE TYPE document_file_type AS ENUM ('pdf', 'md');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'chat_role') THEN
    CREATE TYPE chat_role AS ENUM ('user', 'assistant', 'system');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'analytics_event_type') THEN
    CREATE TYPE analytics_event_type AS ENUM ('open', 'click', 'question', 'answer', 'download_pdf');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'job_status') THEN
    CREATE TYPE job_status AS ENUM ('queued', 'running', 'succeeded', 'failed');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'job_type') THEN
    CREATE TYPE job_type AS ENUM ('index_file_store', 'reindex_document', 'takedown_page');
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'job_event_level') THEN
    CREATE TYPE job_event_level AS ENUM ('info', 'warn', 'error');
  END IF;
END $$;

-- ---------- UPDATED_AT TRIGGER ----------
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------- TABLES ----------
CREATE TABLE IF NOT EXISTS recipients (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name             text NOT NULL,
  email            text NOT NULL,
  company_name     text NULL,
  persona          text NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recipients_email ON recipients (email);

CREATE TABLE IF NOT EXISTS file_stores (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name              text NOT NULL UNIQUE,
  description       text NULL,
  gemini_store_name text NOT NULL UNIQUE,  -- e.g. fileSearchStores/...
  chunking_config   jsonb NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS trg_file_stores_updated_at ON file_stores;
CREATE TRIGGER trg_file_stores_updated_at
BEFORE UPDATE ON file_stores
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS documents (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type          document_source_type NOT NULL DEFAULT 'storage',
  file_type            document_file_type NOT NULL,
  mime_type            text NULL,
  original_filename    text NULL,
  title                text NOT NULL,
  internal_description text NULL, -- NOT user-facing; backend prompt use
  storage_bucket       text NULL,
  storage_path         text NULL, -- relative path for re-embedding later
  external_url         text NULL,
  sha256               text NULL,
  size_bytes           bigint NULL,
  created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_documents_storage_path ON documents (storage_path);
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents (sha256);

-- Bridge: file stores <-> documents (many-to-many)
CREATE TABLE IF NOT EXISTS file_store_documents (
  file_store_id uuid NOT NULL REFERENCES file_stores(id) ON DELETE CASCADE,
  document_id   uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  added_at      timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (file_store_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_fsd_document_id ON file_store_documents (document_id);

-- Pages (public via secret slug)
CREATE TABLE IF NOT EXISTS pages (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug                  text NOT NULL UNIQUE,  -- secret slug
  title                 text NOT NULL,
  recipient_id          uuid NOT NULL REFERENCES recipients(id) ON DELETE RESTRICT,
  template_key          text NOT NULL,
  system_prompt_template text NOT NULL,
  summary_markdown      text NULL,
  details_markdown      text NULL,
  is_active             boolean NOT NULL DEFAULT true,
  takedown_at           timestamptz NULL,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS trg_pages_updated_at ON pages;
CREATE TRIGGER trg_pages_updated_at
BEFORE UPDATE ON pages
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS idx_pages_is_active ON pages (is_active);

-- Bridge: pages <-> file stores (multiple allowed)
CREATE TABLE IF NOT EXISTS page_file_stores (
  page_id      uuid NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  file_store_id uuid NOT NULL REFERENCES file_stores(id) ON DELETE RESTRICT,
  PRIMARY KEY (page_id, file_store_id)
);

CREATE INDEX IF NOT EXISTS idx_pfs_file_store_id ON page_file_stores (file_store_id);

-- Which docs to display on page (subset)
CREATE TABLE IF NOT EXISTS page_documents (
  page_id        uuid NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  document_id    uuid NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
  display_title  text NOT NULL,
  display_caption text NOT NULL,
  sort_order     int NOT NULL DEFAULT 0,
  PRIMARY KEY (page_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_page_documents_sort ON page_documents (page_id, sort_order);

-- Chat sessions & messages (public users; anyone with link)
CREATE TABLE IF NOT EXISTS chat_sessions (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id       uuid NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  created_at    timestamptz NOT NULL DEFAULT now(),
  last_seen_at  timestamptz NOT NULL DEFAULT now(),
  ip_hash       text NULL,
  user_agent    text NULL,
  metadata      jsonb NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_page_id ON chat_sessions (page_id);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    uuid NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role          chat_role NOT NULL,
  content       text NOT NULL,
  model         text NULL,
  citations     jsonb NULL,
  tokens_in     int NULL,
  tokens_out    int NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session_created ON chat_messages (session_id, created_at);

-- Analytics events
CREATE TABLE IF NOT EXISTS analytics_events (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id      uuid NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
  session_id   uuid NULL REFERENCES chat_sessions(id) ON DELETE SET NULL,
  event_type   analytics_event_type NOT NULL,
  payload      jsonb NULL,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_analytics_page_time ON analytics_events (page_id, created_at);

-- Ingestion jobs (async progress)
CREATE TABLE IF NOT EXISTS ingestion_jobs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type      job_type NOT NULL,
  status        job_status NOT NULL DEFAULT 'queued',
  file_store_id uuid NULL REFERENCES file_stores(id) ON DELETE SET NULL,
  progress      int NOT NULL DEFAULT 0,
  total         int NOT NULL DEFAULT 0,
  error         text NULL,
  payload       jsonb NULL,
  result        jsonb NULL,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS trg_ingestion_jobs_updated_at ON ingestion_jobs;
CREATE TRIGGER trg_ingestion_jobs_updated_at
BEFORE UPDATE ON ingestion_jobs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS idx_jobs_status ON ingestion_jobs (status);

-- Job events (UI progress messages)
CREATE TABLE IF NOT EXISTS ingestion_job_events (
  id         bigserial PRIMARY KEY,
  job_id     uuid NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
  ts         timestamptz NOT NULL DEFAULT now(),
  level      job_event_level NOT NULL DEFAULT 'info',
  message    text NOT NULL,
  data       jsonb NULL
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_ts ON ingestion_job_events (job_id, ts);
"""

def main():
    # Connect in autocommit for DDL; transaction pooler safe.
    conn = psycopg2.connect(
        DATABASE_URL,
    )
    conn.autocommit = True

    try:
        with conn.cursor() as cur:
            print("Applying schema...")
            cur.execute(DDL)
            print("✅ Schema applied successfully.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
