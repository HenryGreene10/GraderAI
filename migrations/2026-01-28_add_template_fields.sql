-- Migration: add master key template fields to assignments
BEGIN;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_storage_path text;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_width_px integer;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_height_px integer;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_regions_json jsonb;

ALTER TABLE IF EXISTS assignments
  ADD COLUMN IF NOT EXISTS template_version integer;

COMMIT;
