-- Migration: add page metadata for multi-page scan uploads
BEGIN;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS page_count integer;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS page_sizes_json jsonb;

COMMIT;
