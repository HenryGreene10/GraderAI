-- Migration: add OCR boxes, verdicts, and graded PDF path to uploads
-- Safe to run multiple times due to IF NOT EXISTS

BEGIN;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS ocr_boxes jsonb;

ALTER TABLE IF EXISTS uploads
  ADD COLUMN IF NOT EXISTS verdicts jsonb;

ALTER TABLE IF NOT EXISTS uploads
  ADD COLUMN IF NOT EXISTS graded_pdf_path text;

COMMIT;

