-- Migration: add master key metadata fields to assignments
BEGIN;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_upload_id text;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_original_name text;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_uploaded_at timestamptz;

COMMIT;
