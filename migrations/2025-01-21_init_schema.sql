-- Initial schema for GraderAI MVP (safe to re-run)
BEGIN;

CREATE TABLE IF NOT EXISTS assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL,
  title text NOT NULL,
  due_date date,
  rubric_json jsonb,
  rubric_version text,
  prompt_version text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS uploads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid NOT NULL,
  assignment_id uuid REFERENCES assignments(id) ON DELETE SET NULL,
  storage_path text NOT NULL,
  original_name text,
  mime_type text,
  size_bytes bigint,
  status text NOT NULL DEFAULT 'pending',
  ocr_status text,
  ocr_text text,
  ocr_boxes jsonb,
  ocr_confidence numeric,
  ocr_error text,
  extracted_text text,
  grade_json jsonb,
  overlay_json jsonb,
  overlay_path text,
  graded_pdf_path text,
  needs_review boolean NOT NULL DEFAULT false,
  rubric_version text,
  prompt_version text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS overrides (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  upload_id uuid NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
  owner_id uuid NOT NULL,
  overrides_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

-- Backfill columns if uploads already exists
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS assignment_id uuid;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS storage_path text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS original_name text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS mime_type text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS size_bytes bigint;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS status text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS ocr_status text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS ocr_text text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS ocr_boxes jsonb;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS ocr_confidence numeric;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS ocr_error text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS extracted_text text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS grade_json jsonb;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS overlay_json jsonb;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS overlay_path text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS graded_pdf_path text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS needs_review boolean;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS rubric_version text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS prompt_version text;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS created_at timestamptz;
ALTER TABLE IF EXISTS uploads ADD COLUMN IF NOT EXISTS updated_at timestamptz;

CREATE INDEX IF NOT EXISTS idx_assignments_owner ON assignments(owner_id);
CREATE INDEX IF NOT EXISTS idx_uploads_owner ON uploads(owner_id);
CREATE INDEX IF NOT EXISTS idx_uploads_assignment ON uploads(assignment_id);
CREATE INDEX IF NOT EXISTS idx_overrides_upload ON overrides(upload_id);

COMMIT;
