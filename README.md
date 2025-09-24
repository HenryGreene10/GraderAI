# Assignments OCR (Web App)

What it is: A web app for uploading student files, running OCR, and viewing extracted text.

What powers it: React (frontend), FastAPI (backend), Supabase (auth/db/storage).

What customers do:
- Sign in
- Upload files
- Wait for OCR
- Read/download results

Where configuration lives: `.env` files (samples provided).

Privacy/Security:
- Never share service keys
- Only publishable/anon keys appear in the browser

Support/Troubleshooting (very brief):
- If uploads appear stuck: your org’s admin should verify OCR is enabled, and that the backend is reachable.
- If you see “Forbidden”: you’re not signed in or don’t own the file.
- For admins only: see the `.env.example` files for the environment variables to set.

Policy note: OCR may return approximations; verify before grading.
