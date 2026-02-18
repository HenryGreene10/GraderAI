# GraderAI

GraderAI is a web app that auto-grades handwritten worksheets and produces a printable, marked PDF.

## Beta workflow (PDF-first)
1) Create an assignment.
2) Upload a scanned **Master Key PDF** (first page is used for key extraction).
3) Wait for strict quality gate status:
   - `READY`: grading unlocked.
   - `NEEDS_REUPLOAD`: extraction degraded; reupload a cleaner scan.
4) Upload student **PDF** submissions.
5) Download graded/marked PDFs.

## Quality gates
- Master key approval is fail-closed: degraded extraction warnings block approval.
- Student grading is blocked unless master key status is `READY`.
- Overlay rendering uses a single coordinate contract to prevent scale drift.

## Developer setup

Backend:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000
```

Frontend:
```bash
npm --prefix frontend install
npm --prefix frontend run dev
```

### Environment variables (required)
Backend:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `AZURE_OCR_ENDPOINT`
- `AZURE_OCR_KEY`
- `CORS_ALLOW_ORIGINS`

Frontend:
- `VITE_BACKEND_URL`
- `VITE_SUPABASE_URL`
- `VITE_SUPABASE_ANON_KEY`
- `VITE_API_BASE_URL` (required for API calls)
- `VITE_DEV_PROXY_TARGET` (dev proxy target; default `http://127.0.0.1:8000`)

## Sample flow (developer)
1) Create assignment.
2) Upload Master Key PDF/template (`POST /api/assignments/{id}/template`).
3) Verify assignment shows master key status `READY`.
4) Upload 1–2 student PDFs.
5) Click retry/grade (`POST /api/uploads/{id}/retry`) if needed.
6) Inspect debug bundle artifacts if needed.

### Debug artifacts
Enable with `DEBUG_ARTIFACTS=1` or `?debug=1`.

Artifacts stored in:
```
submissions/{owner_id}/debug/{upload_id}/
```
Includes:
- `normalized.png`
- `ocr_raw.json`
- `ocr_overlay.png`
- `template_overlay.png`
- `marks_overlay.png`
- `marked.pdf`

## Notes & limitations
- Single-page worksheets only (MVP).
- Best results with clean, high-contrast scanned PDFs.
- Regions remove ambiguity: each answer box is tied to its region and Q label.
- If template alignment fails, GraderAI returns an error and requests a review.

## Privacy & security
- Never share service keys.
- Only publishable/anon keys appear in the browser.
