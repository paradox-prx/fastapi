CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS recipients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL,
  email text NOT NULL,
  company_name text NOT NULL,
  persona text NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS file_stores (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE,
  description text NULL,
  gemini_store_name text NOT NULL UNIQUE,
  chunking_config jsonb NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type text NOT NULL,
  mime_type text NOT NULL,
  original_filename text NULL,
  storage_bucket text NULL,
  storage_path text NULL,
  external_url text NULL,
  title text NOT NULL,
  internal_description text NULL,
  sha256 text NULL,
  size_bytes bigint NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS file_store_documents (
  file_store_id uuid NOT NULL REFERENCES file_stores(id),
  document_id uuid NOT NULL REFERENCES documents(id),
  added_at timestamptz DEFAULT now(),
  PRIMARY KEY (file_store_id, document_id)
);

CREATE TABLE IF NOT EXISTS pages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  slug text NOT NULL UNIQUE,
  title text NOT NULL,
  recipient_id uuid NOT NULL REFERENCES recipients(id),
  template_key text NOT NULL,
  system_prompt_template text NOT NULL,
  summary_markdown text NULL,
  details_markdown text NULL,
  is_active boolean DEFAULT true,
  takedown_at timestamptz NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS page_file_stores (
  page_id uuid NOT NULL REFERENCES pages(id),
  file_store_id uuid NOT NULL REFERENCES file_stores(id),
  PRIMARY KEY (page_id, file_store_id)
);

CREATE TABLE IF NOT EXISTS page_documents (
  page_id uuid NOT NULL REFERENCES pages(id),
  document_id uuid NOT NULL REFERENCES documents(id),
  display_title text NOT NULL,
  display_caption text NOT NULL,
  sort_order int DEFAULT 0,
  PRIMARY KEY (page_id, document_id)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id uuid NOT NULL REFERENCES pages(id),
  created_at timestamptz DEFAULT now(),
  last_seen_at timestamptz NULL,
  ip_hash text NULL,
  user_agent text NULL,
  metadata jsonb NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id uuid REFERENCES chat_sessions(id),
  role text NOT NULL,
  content text NOT NULL,
  model text NULL,
  citations jsonb NULL,
  tokens_in int NULL,
  tokens_out int NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id uuid REFERENCES pages(id),
  session_id uuid NULL REFERENCES chat_sessions(id),
  event_type text NOT NULL,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type text NOT NULL,
  status text NOT NULL,
  file_store_id uuid NOT NULL REFERENCES file_stores(id),
  progress int DEFAULT 0,
  total int DEFAULT 0,
  error text NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingestion_job_events (
  id bigserial PRIMARY KEY,
  job_id uuid REFERENCES ingestion_jobs(id),
  ts timestamptz DEFAULT now(),
  level text NOT NULL,
  message text NOT NULL,
  data jsonb NULL
);

CREATE INDEX IF NOT EXISTS idx_pages_slug ON pages (slug);
CREATE INDEX IF NOT EXISTS idx_documents_storage_path ON documents (storage_path);
CREATE INDEX IF NOT EXISTS idx_analytics_page_time ON analytics_events (page_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session_time ON chat_messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON ingestion_job_events (job_id, id);
