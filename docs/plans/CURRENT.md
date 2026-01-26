# Current Plan (GraderAI MVP)

## Current objective
Make the UI the source of truth and validate the real workflow: upload -> auto OCR -> grade -> PDF -> override.

## Supabase setup checklist
- Status: DONE / verified for local dev.
- Migration applied: `migrations/2025-01-21_init_schema.sql`.
- Buckets created: `submissions`, `graded-pdfs`, `overlays`.
- RLS policies verified:
  - Tables: owner/teacher rows scoped by `auth.uid()`.
  - Storage: enforce `auth.uid()` prefix in object paths and bucket name allowlists.

## Local dev setup (short)
- Backend env includes `SUPABASE_JWT_SECRET` (JWT auth).
- Frontend uses `frontend/.env.local` with `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_API_BASE_URL`.
- Do not commit `.env.local`.

## Backend env vars
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_JWT_SECRET`
- `SUBMISSIONS_BUCKET`
- `GRADED_BUCKET`
- `OVERLAYS_BUCKET`
- `CORS_ALLOW_ORIGINS`
- Optional OCR: `OCR_*`

## Tickets

### Ticket 1 — Supabase foundation (DONE)
Scope: schema, buckets, RLS, and verified backend read/write via service role.
Acceptance checks
- Migration applied and schema matches `migrations/2025-01-21_init_schema.sql`.
- Buckets exist with expected names.
- RLS policies enforce `auth.uid()` ownership and storage path prefix constraints.
- Backend read/write succeeds to tables and buckets.

### Ticket 2 — Local dev wiring (DONE)
Scope: local env wiring for frontend and backend auth.
Acceptance checks
- `frontend/.env.local` drives Supabase client via `import.meta.env`.
- `VITE_API_BASE_URL` routes frontend to local backend.
- App no longer shows "Missing Supabase env" when vars are present.

### Ticket 3 — Uploads UI becomes source of truth (DONE)
Scope: the uploads list is the canonical view for storage + DB state.
Acceptance checks
- Uploaded items appear in the UI list.
- Preview works for each upload.
- Delete removes the storage object and its DB row.
Notes
- Upload succeeds and preview shows the original file.
- Delete is available.
- Known non-blocking toast issue deferred.

### Ticket 4 — OCR pipeline (internal only) (TODO)
Scope: auto-run OCR after upload for downstream grading only.
Acceptance checks
- Upload triggers OCR automatically.
- OCR result is persisted and accessible to backend grading logic.
- No frontend UI added for viewing OCR output.

### Ticket 5 — Grade + PDF (TODO)
Scope: use OCR output to identify questions/answers and generate a marked PDF.
Acceptance checks
- Teacher sees the original uploaded file and the marked PDF.
Note
- Layout/region accuracy is approximate in MVP; refinement deferred.

### Ticket 6 — Overrides (TODO)
Scope: teacher can adjust grading via a later workflow (chat-based or manual edits to marked PDF).
Acceptance checks
- Do not design override UI yet.

### Ticket 7 — Frontend polish / encoding fixes (LATER)
Scope: UI cleanup, encoding warnings removed, and small UX refinements.
Acceptance checks
- No console warnings related to invalid characters/encoding.
- Dashboard and Assignments pages render cleanly.
- Visual polish pass applied to upload + results views.
