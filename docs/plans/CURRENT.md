# Current Plan (GraderAI MVP)

## Current objective
Stabilize the simplified backend + Supabase setup and align the frontend with the new API contracts so grading flow (OCR -> grade -> PDF -> overrides) can be validated end-to-end.

## Supabase setup checklist
- Apply `migrations/2025-01-21_init_schema.sql` to the project database.
- Create storage buckets: `submissions`, `graded-pdfs`, `overlays`.
- RLS policies summary:
  - Tables: owner/teacher rows are scoped by `auth.uid()`.
  - Storage: enforce `auth.uid()` prefix in object paths and bucket name allowlists.

## Backend env vars
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUBMISSIONS_BUCKET`
- `GRADED_BUCKET`
- `OVERLAYS_BUCKET`
- `CORS_ALLOW_ORIGINS`
- Optional OCR: `OCR_*`

## Tickets

### 1) Supabase setup + verification - [x] DONE — Supabase setup + verification
Short scope: create buckets and RLS; verify backend can read/write.
Status: DONE (schema applied; buckets created; storage + table RLS enforced; auth.uid() prefix; authenticated-only)
Notes: Policy names messy but conditions verified identical; leaving as-is.

Acceptance checks
- Migration applied and schema matches `migrations/2025-01-21_init_schema.sql`.
- Buckets exist with expected names.
- RLS policies enforce `auth.uid()` ownership and storage path prefix constraints.
- Backend health: basic read/write to each bucket succeeds via service role.

### 2) Frontend update to new endpoints
Notes: Backend auth verifies Supabase JWT (Authorization: Bearer ...) and uses sub for ownership.
Update client calls to use the simplified backend routes:
- `POST /api/ocr/start`
- `GET /api/ocr/status/{id}`
- `POST /api/grade`
- `POST /api/override`

Acceptance checks
- New endpoints are called with expected payloads.
- OCR flow: start -> poll status -> results displayed.
- Grade flow: submission -> grades -> overlays/PDF links render.
- Override flow: update persists and re-renders.

### 3) Frontend encoding fixes
Resolve encoding issues in UI rendering and content handling.

Acceptance checks
- `frontend/src/pages/Dashboard.jsx` renders without encoding glitches.
- `frontend/src/pages/AssignmentsPage.jsx` renders without encoding glitches.
- No console warnings related to invalid characters/encoding.
