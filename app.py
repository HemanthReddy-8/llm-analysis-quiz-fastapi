"""
FastAPI app to accept quiz tasks and solve them using Playwright headless browser.
Run locally:
  uvicorn app:app --host 0.0.0.0 --port 8000
"""

# Ensure ProactorEventLoop on Windows so subprocesses work (Playwright)
import sys
if sys.platform.startswith("win"):
    try:
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

# --- imports ---
import asyncio
import json
import re
import tempfile
import time
import os
from typing import Optional
from urllib.parse import urljoin
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.responses import JSONResponse
import httpx

# Playwright imports (after proactor policy)
from playwright.async_api import async_playwright

# PDF parsing
import pdfplumber
import traceback
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# --- end imports ---

# Config
SECRET = "PolireddyPalem"  # keep in sync with Google Form
DEBUG_DIR = Path("debug_artifacts")
DEBUG_DIR.mkdir(exist_ok=True)

# make these configurable if you want
TAKE_SCREENSHOTS = True
SAVE_HTML = True
USER_AGENT = "LLM-Quiz-Solver/1.0 (+https://github.com/yourname)"


app = FastAPI()


class QuizRequest(BaseModel):
    email: str
    secret: str
    url: str


async def write_last_submit(submit_url: str, payload: dict, resp_diag: dict):
    """Save last submit diagnostic to disk for post-mortem."""
    try:
        out = {
            "timestamp": time.time(),
            "submit_url": submit_url,
            "payload": payload,
            "response": resp_diag
        }
        p = DEBUG_DIR / "last_submit.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        logger.info("Wrote last submit diagnostic to %s", str(p))
    except Exception as e:
        logger.error("Failed to write last_submit.json: %s", str(e))


async def submit_with_fallback(submit_url: str, payload: dict, timeout: int = 60) -> dict:
    """
    Try POSTing JSON to submit_url. If 405, try GET with query params.
    If POST returns 200 with empty body, try GET or follow Location header.
    Saves last_submit.json and returns parsed JSON or diagnostic dict.
    """
    logger.info("Submitting to %s payload=%s", submit_url, payload)
    headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        # Try POST first
        try:
            resp = await client.post(submit_url, json=payload, headers=headers)
        except Exception as e:
            diag = {"status_code": None, "text": str(e), "headers": {}}
            await write_last_submit(submit_url, payload, diag)
            logger.error("POST to %s failed: %s", submit_url, str(e))
            return diag

        logger.info("POST %s -> %s", submit_url, resp.status_code)
        # Try parse JSON
        try:
            parsed = resp.json()
            await write_last_submit(submit_url, payload, parsed)
            return parsed
        except Exception:
            # Not JSON. Collect diagnostics.
            text = resp.text or ""
            headers_resp = dict(resp.headers or {})

            # If status 405, try GET with params
            if resp.status_code == 405:
                logger.warning("POST returned 405; trying GET fallback for %s", submit_url)
                try:
                    resp2 = await client.get(submit_url, params=payload, headers={"User-Agent": USER_AGENT})
                    logger.info("GET %s -> %s", submit_url, resp2.status_code)
                    try:
                        parsed2 = resp2.json()
                        await write_last_submit(submit_url, payload, parsed2)
                        return parsed2
                    except Exception:
                        diag = {"status_code": resp2.status_code, "text": resp2.text, "headers": dict(resp2.headers or {})}
                        await write_last_submit(submit_url, payload, diag)
                        return diag
                except Exception as e2:
                    diag = {"status_code": None, "text": str(e2), "headers": headers_resp}
                    await write_last_submit(submit_url, payload, diag)
                    logger.error("GET fallback to %s failed: %s", submit_url, str(e2))
                    return diag

            # If 200 with empty body, try follow Location header or do GET
            if resp.status_code == 200 and (not text.strip()):
                loc = headers_resp.get("Location") or headers_resp.get("location")
                if loc:
                    logger.info("Following Location header: %s", loc)
                    try:
                        next_url = urljoin(submit_url, loc)
                        resp3 = await client.get(next_url, headers={"User-Agent": USER_AGENT})
                        try:
                            parsed3 = resp3.json()
                            await write_last_submit(submit_url, payload, parsed3)
                            return parsed3
                        except Exception:
                            diag = {"status_code": resp3.status_code, "text": resp3.text, "headers": dict(resp3.headers or {})}
                            await write_last_submit(submit_url, payload, diag)
                            return diag
                    except Exception as e3:
                        diag = {"status_code": resp.status_code, "text": text, "headers": headers_resp}
                        await write_last_submit(submit_url, payload, diag)
                        logger.error("Following Location failed: %s", str(e3))
                        return diag

                # Try GET on submit_url
                logger.info("POST returned 200 with empty body; trying GET on %s", submit_url)
                try:
                    resp4 = await client.get(submit_url, params=payload, headers={"User-Agent": USER_AGENT})
                    try:
                        parsed4 = resp4.json()
                        await write_last_submit(submit_url, payload, parsed4)
                        return parsed4
                    except Exception:
                        diag = {"status_code": resp4.status_code, "text": resp4.text, "headers": dict(resp4.headers or {})}
                        await write_last_submit(submit_url, payload, diag)
                        return diag
                except Exception as e4:
                    diag = {"status_code": resp.status_code, "text": text, "headers": headers_resp}
                    await write_last_submit(submit_url, payload, diag)
                    logger.error("GET attempt after empty POST failed: %s", str(e4))
                    return diag

            # otherwise return diagnostic
            diag = {"status_code": resp.status_code, "text": text, "headers": headers_resp}
            await write_last_submit(submit_url, payload, diag)
            return diag


def extract_secret_from_text(text: str) -> Optional[str]:
    """
    Heuristic extractor for a 'secret code' from page text.
    """
    m = re.search(r'["\']secret["\']\s*:\s*["\']([^"\']+)["\']', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'secret[^A-Za-z0-9]*([A-Za-z0-9_\-]{3,})', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    m = re.search(r'["\']([A-Za-z0-9_\-]{3,})["\']', text)
    if m:
        return m.group(1).strip()
    m = re.search(r'\b([A-Za-z0-9]{4,})\b', text)
    if m:
        return m.group(1).strip()
    return None


def find_submit_url(html: str) -> Optional[str]:
    m = re.search(r'https?://[\w./\-?=&%]+/submit[\w/\-?=&%]*', html)
    if m:
        return m.group(0)
    m2 = re.search(r'https?://[\w./\-?=&%]+', html)
    if m2:
        return m2.group(0)
    return None


def find_pdf_link(html: str, base_url: str = '') -> Optional[str]:
    m = re.search(r'href=["\']([^"\']+\.pdf)["\']', html, re.IGNORECASE)
    if m:
        link = m.group(1)
        if link.startswith('http'):
            return link
        if base_url.endswith('/'):
            return base_url.rstrip('/') + '/' + link.lstrip('/')
        return base_url + link
    return None


async def download_bytes(url: str) -> bytes:
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.content
    except Exception as e:
        logger.error("download_bytes failed for %s: %s", url, str(e))
        raise


def parse_pdf_sum(path: str) -> Optional[float]:
    try:
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) >= 2:
                page = pdf.pages[1]
            else:
                page = pdf.pages[0]
            tables = page.extract_tables()
            if tables:
                tbl = tables[0]
                headers = [c.strip().lower() if c else '' for c in tbl[0]]
                col_idx = None
                for i, h in enumerate(headers):
                    if 'value' in h:
                        col_idx = i
                        break
                if col_idx is None:
                    col_idx = len(headers) - 1
                s = 0.0
                for row in tbl[1:]:
                    cell = row[col_idx]
                    if cell is None:
                        continue
                    num = re.sub(r'[^0-9.-]', '', cell)
                    try:
                        s += float(num)
                    except Exception:
                        continue
                return s
    except Exception:
        return None
    return None


def extract_numeric_answer_from_text(text: str) -> Optional[float]:
    m = re.search(r'sum[^\d\n]*([0-9,]+(?:\.[0-9]+)?)', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', ''))
        except:
            pass
    m2 = re.search(r'([0-9]{2,}[0-9,]*)', text)
    if m2:
        try:
            return float(m2.group(1).replace(',', ''))
        except:
            pass
    return None


async def save_debug_step(step_index: int, page, content_html: str):
    """Save screenshot and HTML for the debugging step."""
    try:
        if TAKE_SCREENSHOTS:
            path = DEBUG_DIR / f"dbg_step_{step_index}.png"
            await page.screenshot(path=str(path), full_page=True)
            logger.info("Saved screenshot %s", path)
        if SAVE_HTML:
            html_path = DEBUG_DIR / f"dbg_step_{step_index}.html"
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(content_html)
            logger.info("Saved HTML %s", html_path)
    except Exception as e:
        logger.error("Failed saving debug artifacts: %s", str(e))


async def solve_quiz_url(initial_url: str, original_payload: dict) -> dict:
    """
    Chain-following solver with debug artifact saving enabled.
    """
    timeout_seconds = 180.0
    start_time = time.time()
    current_url = initial_url
    last_response = {"correct": False, "reason": "No attempts made"}
    step_index = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        # set UA for page requests
        try:
            await page.set_extra_http_headers({"User-Agent": USER_AGENT})
        except Exception:
            pass

        while True:
            if time.time() - start_time > timeout_seconds:
                await browser.close()
                return {"correct": False, "reason": "Timeout exceeded (3 minutes)", "last_response": last_response}

            step_index += 1
            await page.goto(current_url, wait_until='networkidle', timeout=60000)
            await asyncio.sleep(1)
            content_html = await page.content()
            try:
                content_text = await page.inner_text('body')
            except Exception:
                content_text = re.sub(r'<[^>]+>', ' ', content_html)

            # save debug artifacts
            await save_debug_step(step_index, page, content_html)

            # ----- Scrape instruction handling -----
            scrape_match = re.search(r'\bScrape\s+([^\s\(\n\r]+)', content_text, re.IGNORECASE)
            if scrape_match:
                scrape_path = scrape_match.group(1).strip()
                scrape_url = urljoin(current_url, scrape_path)
                try:
                    await page.goto(scrape_url, wait_until='networkidle', timeout=60000)
                    await asyncio.sleep(0.5)
                    try:
                        scrape_text = await page.inner_text('body')
                    except Exception:
                        scrape_text = await page.content()
                    # save step for scrape page
                    await save_debug_step(step_index + 1000, page, await page.content())
                except Exception:
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            r = await client.get(scrape_url, headers={"User-Agent": USER_AGENT})
                            r.raise_for_status()
                            scrape_text = r.text
                    except Exception as e:
                        await browser.close()
                        return {"correct": False, "reason": "Failed to fetch scrape URL", "scrape_url": scrape_url, "error": str(e)}

                secret_code = extract_secret_from_text(scrape_text)
                if not secret_code:
                    await browser.close()
                    return {"correct": False, "reason": "Could not find secret on scrape page", "scrape_url": scrape_url, "page_snippet": scrape_text[:800]}

                submit_url = find_submit_url(content_html) or urljoin(current_url, "/submit")
                submit_payload = {
                    "email": original_payload.get("email"),
                    "secret": original_payload.get("secret"),
                    "url": current_url,
                    "answer": secret_code
                }

                resp_json = await submit_with_fallback(submit_url, submit_payload)
                last_response = resp_json
                next_url = resp_json.get("url")
                if next_url:
                    current_url = urljoin(current_url, next_url)
                    continue
                else:
                    await browser.close()
                    return last_response

            # ----- demo pattern: POST this JSON to <url> -----
            demo_match = re.search(r'POST\s+this\s+JSON\s+to\s+(https?://[^\s\n\r]+)', content_text, re.IGNORECASE)
            if demo_match:
                submit_url = demo_match.group(1).strip()
                start_pos = content_text.find('{', demo_match.end())
                json_payload = {}
                if start_pos != -1:
                    depth = 0
                    end_pos = -1
                    for i in range(start_pos, len(content_text)):
                        ch = content_text[i]
                        if ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                end_pos = i
                                break
                    if end_pos != -1:
                        raw_json = content_text[start_pos:end_pos+1]
                        try:
                            json_payload = json.loads(raw_json)
                        except Exception:
                            try:
                                cleaned = re.sub(r',\s*([}\]])', r'\1', raw_json)
                                json_payload = json.loads(cleaned)
                            except Exception:
                                json_payload = {}

                json_payload.setdefault("email", original_payload.get("email"))
                json_payload.setdefault("secret", original_payload.get("secret"))
                json_payload.setdefault("url", current_url)
                json_payload.setdefault("answer", "I solved it")

                resp_json = await submit_with_fallback(submit_url, json_payload)
                last_response = resp_json
                next_url = resp_json.get("url")
                if next_url:
                    current_url = urljoin(current_url, next_url)
                    continue
                else:
                    await browser.close()
                    return last_response

            # ----- fallback: PDF / numeric / generic submit -----
            submit_url = find_submit_url(content_html)
            pdf_link = find_pdf_link(content_html, base_url=current_url)
            if pdf_link:
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as f:
                    pdf_bytes = await download_bytes(pdf_link)
                    f.write(pdf_bytes)
                    f.flush()
                    fpath = f.name
                result = parse_pdf_sum(fpath)
                answer = result
            else:
                answer = extract_numeric_answer_from_text(content_text)
                if answer is None:
                    await browser.close()
                    return {"correct": False, "reason": "Could not auto-solve", "page_text_snippet": content_text[:1200], "last_response": last_response}

            submit_payload = {
                "email": original_payload.get('email'),
                "secret": original_payload.get('secret'),
                "url": current_url,
                "answer": answer
            }

            if not submit_url:
                await browser.close()
                return {"correct": False, "reason": "No submit URL found", "attempted_answer": submit_payload, "last_response": last_response}

            resp_json = await submit_with_fallback(submit_url, submit_payload)
            last_response = resp_json
            next_url = resp_json.get("url")
            if next_url:
                current_url = urljoin(current_url, next_url)
                continue
            else:
                await browser.close()
                return last_response


@app.post("/api/quiz")
async def handle_quiz(payload: QuizRequest):
    """
    Accept a validated Pydantic payload. Returns solver result.
    If an exception occurs, return the full traceback in 'error' for debugging.
    """
    data = payload.dict()
    if data.get("secret") != SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    url = data["url"]
    if url == "TEST_NO_BROWSER":
        return JSONResponse(status_code=200, content={
            "correct": False,
            "reason": "test mode - no browser launched",
            "attempted_payload": data
        })

    try:
        answer_payload = await solve_quiz_url(url, data)
        return JSONResponse(status_code=200, content=answer_payload)
    except Exception:
        tb = traceback.format_exc()
        logger.error("Exception while solving quiz:\n%s", tb)
        return JSONResponse(status_code=500, content={"error": tb})
