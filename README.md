# LLM Analysis Quiz — FastAPI solver

Features:
- FastAPI endpoint /api/quiz accepts POST JSON {email, secret, url}
- Uses Playwright to render JS pages, solves demo flows, follows chained URLs
- Saves debug artifacts in debug_artifacts/
- submit_with_fallback does POST -> GET fallback and writes last_submit.json

Run locally:
1. python -m venv .venv
2. .venv\\Scripts\\activate (Windows) or source .venv/bin/activate
3. pip install -r requirements.txt
4. playwright install
5. uvicorn app:app --host 0.0.0.0 --port 8000

Testing:
curl -X POST http://127.0.0.1:8000/api/quiz -H "Content-Type: application/json" -d '{"email":"you@example.com","secret":"PolireddyPalem","url":"TEST_NO_BROWSER"}'

License: MIT
