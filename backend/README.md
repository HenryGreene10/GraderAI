Backend development and testing
===============================

Setup
- python -m venv .venv && source .venv/bin/activate
- pip install -r backend/requirements.txt

Run server (dev)
- uvicorn backend.app:app --reload --host 0.0.0.0 --port 8000

Run tests
- pip install -r backend/requirements-dev.txt
- pytest -q

Notes
- Tests mock Supabase and OCR; no network calls occur.
- Set OCR_MOCK=1 in dev to short-circuit OCR while wiring the pipeline.
- Ensure required env vars are set (see AGENTS.md Security & Configuration Tips).

OCR Modes
---------
- OCR_MOCK=1
  - Uses MockOCRProvider for fast, deterministic development without network.
- OCR_PROVIDER=hf (default) with HF_TOKEN (and optional HF_API_URL)
  - Uses Hugging Face Inference API via httpx (timeout=60s).
  - Authorization header: `Authorization: Bearer ${HF_TOKEN}`.
- Stable surface
  - `extract_text(image_bytes|image_url)` returns `{ text, pages, confidence }` regardless of provider.

Sample .env (local dev)
SUPABASE_URL=...your supabase url...
SUPABASE_SERVICE_ROLE_KEY=...service role key...
SUPABASE_BUCKET=submissions
CORS_ALLOW_ORIGINS=http://localhost:5173
OCR_PROVIDER=hf
OCR_MOCK=0
HF_TOKEN=...your hf token...
HF_API_URL=https://api-inference.huggingface.co/models/<modelId>

Sample .env (CI)
SUPABASE_URL=http://example.local
SUPABASE_SERVICE_ROLE_KEY=dummy
SUPABASE_BUCKET=submissions
CORS_ALLOW_ORIGINS=http://localhost:5173
OCR_PROVIDER=hf
OCR_MOCK=1
HF_TOKEN=dummy
HF_API_URL=https://api-inference.huggingface.co/models/test-model
