-- Migration: track template version used for grading
BEGIN;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS template_version_used integer;

COMMIT;
