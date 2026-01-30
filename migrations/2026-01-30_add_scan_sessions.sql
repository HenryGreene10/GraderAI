-- Migration: add scan_sessions table for scan-first flow
BEGIN;

CREATE TABLE IF NOT EXISTS scan_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  token text UNIQUE NOT NULL,
  owner_id uuid NOT NULL,
  assignment_id uuid NOT NULL,
  mode text NOT NULL CHECK (mode IN ('master_key','student')),
  status text NOT NULL DEFAULT 'pending',
  resulting_upload_id uuid NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

ALTER TABLE IF EXISTS scan_sessions ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'scan_sessions' AND policyname = 'scan_sessions_owner_select'
  ) THEN
    CREATE POLICY scan_sessions_owner_select
      ON scan_sessions
      FOR SELECT
      USING (auth.uid() = owner_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'scan_sessions' AND policyname = 'scan_sessions_owner_insert'
  ) THEN
    CREATE POLICY scan_sessions_owner_insert
      ON scan_sessions
      FOR INSERT
      WITH CHECK (auth.uid() = owner_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname = 'public' AND tablename = 'scan_sessions' AND policyname = 'scan_sessions_owner_update'
  ) THEN
    CREATE POLICY scan_sessions_owner_update
      ON scan_sessions
      FOR UPDATE
      USING (auth.uid() = owner_id)
      WITH CHECK (auth.uid() = owner_id);
  END IF;
END $$;

COMMIT;
