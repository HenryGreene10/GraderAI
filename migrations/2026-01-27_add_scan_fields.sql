-- Migration: add scan normalization artifacts to uploads
BEGIN;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS normalized_image_path text;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS normalized_pdf_path text;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS normalized_width_px integer;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS normalized_height_px integer;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS scan_status text;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS scan_error text;

COMMIT;
