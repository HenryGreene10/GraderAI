# Current Plan (GraderAI MVP)

## Current objective
Automate the end-to-end workflow and review UX: upload → auto OCR → auto grade → auto marked PDF → view in-app → review/override.

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

### Ticket 4 — OCR pipeline (internal only) (DONE)
Scope: auto-run OCR after upload for downstream grading only.
Acceptance checks
- Upload triggers OCR automatically.
- OCR result is persisted and accessible to backend grading logic.
- No frontend UI added for viewing OCR output.
Evidence
- Uploads row `d818c641-af5e-4dcf-8369-78b991b1823f` shows `ocr_status=done`, `ocr_text` populated, `ocr_error` null, `ocr_boxes` populated (Azure Read).
Known OCR quirks
- OCR may confuse commas/periods and spelling; grading must be tolerant and rely on rubric rules + context rather than exact string match.

### Ticket 5 — Grade + PDF (DONE)
Scope: use OCR output to identify questions/answers and generate a marked PDF.
Acceptance checks
- Teacher sees the original uploaded file and the marked PDF.
Note
- Layout/region accuracy is approximate in MVP; refinement deferred.

### Ticket 6A — Review state (DONE)
Scope: teacher can mark graded uploads as Reviewed/Overridden with a note; does NOT change grades or PDFs.
Acceptance checks
- State + note persist in DB and survive refresh.
- Badge updates to Reviewed/Overridden in the uploads list.
- Review controls only show for graded uploads.

### Ticket 6B — Real overrides (DESIGN LATER)
Scope: “fix misgrade” without editable PDFs (MVP approach).
Acceptance checks (TBD — design later)
- Teacher can set final score and/or per-question correctness + note.
- UI displays overridden score/state clearly.
- Optional later: regenerate PDF with a small “override stamp” box (no full PDF editing).

### Ticket 7 — Frontend polish + automated workflow (NEXT)
Scope
1) Workflow automation (Option A)
   - Auto-run grade + marked PDF generation after OCR completes.
   - Remove or hide per-row “Generate marked PDF” button (deprecated).
   - Row states reflect progress: Uploading → OCR… → Grading… → PDF ready (or Error).
   - Disable actions during processing; show loading indicators.
2) In-app document viewer (same window)
   - Replace “Open marked PDF” new tab behavior with an in-app viewer (modal/drawer).
   - Viewer supports Original + Marked PDF (tabs/toggle).
   - Download controls inside viewer; optional “Open in new tab” link is secondary.
3) Actions cleanup (reduce button clutter)
   - One primary per-row action: “View”.
   - Secondary actions in overflow menu (⋯): Review/Override (6A), Download original, Download marked PDF, Delete (confirm).
   - Delete is destructive and always confirmed.
4) UX quality / consistency
   - Fix console warnings / encoding issues.
   - Improve empty states (no assignments / no uploads).
   - Consistent toast messages for success/failure.
   - Better responsive spacing for action area.
5) Safety / correctness guards
   - If PDF not ready, hide/disable view-marked and review actions.
   - Show clear error status + “Retry” action for failed OCR/grade/PDF (retry can be stubbed initially).
Acceptance checks
- Uploading a file automatically results in a marked PDF without any manual “Generate” action.
- Marked PDF can be viewed in-app without opening a new browser tab/window.
- Row actions are simplified (View + overflow); no more 5-button row clusters.
- Status/progress chips update correctly and persist after refresh.
- No obvious console warnings; key flows show clear success/error messaging.
