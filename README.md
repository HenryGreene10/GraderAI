# GraderAI

GraderAI is a web app that auto-grades handwritten worksheets and produces a printable, marked PDF.

## What teachers do (2 minutes)
Create one **Master Key Sheet** per assignment (required rules):
1) Draw a **dashed (or thin) outline** around EACH question region.
   - The region must contain the circled Q label and the answer box.
2) Write a **circled Q1/Q2…** label inside each region.
3) Draw a **small solid/thick answer box** inside the region.
4) Write the **correct final answer** inside the answer box.
5) Take a straight-on photo (or scan) and upload as the Master Key.

Then upload student photos. GraderAI aligns each student scan to the Master Key and marks deterministically.

## What GraderAI does
- Normalizes photos (orientation, deskew, perspective).
- Aligns student scans to the Master Key template.
- OCRs each answer box and grades per box.
- Produces a **printable, marked PDF** with ✓/✗ and scores.
- Provides debug overlays to verify alignment when needed.

Best for structured worksheets (elementary math, short answers).

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

## Sample flow (developer)
1) Create assignment.
2) Upload Master Key template (`POST /api/assignments/{id}/template`).
3) Upload 1–2 student scans.
4) Click retry/grade (`POST /api/uploads/{id}/retry`).
5) Inspect debug bundle artifacts if needed.

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
- Best results with high-res, well-lit, straight-on photos.
- Regions remove ambiguity: each answer box is tied to its region and Q label.
- If template alignment fails, GraderAI returns an error and requests a review.

## Privacy & security
- Never share service keys.
- Only publishable/anon keys appear in the browser.
