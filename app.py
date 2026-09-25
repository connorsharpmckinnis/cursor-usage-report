"""FastAPI web app: upload Cursor team usage CSV → interactive report + AI summary."""

from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from ai_summary import build_ai_summary
from generate_report import (
    DEFAULT_SEAT_COST_USD,
    DEFAULT_SEAT_EMAILS,
    account_spend,
    build_report,
    load_usage,
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("USAGE_REPORT_DATA", BASE_DIR / "data" / "uploads"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_BYTES = int(os.environ.get("USAGE_REPORT_MAX_UPLOAD", 40 * 1024 * 1024))
DEFAULT_PORT = int(os.environ.get("PORT", "9003"))

app = FastAPI(title="Cursor / Grok Bot usage report", docs_url=None, redoc_url=None)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

_SAFE_ID = re.compile(r"^[a-f0-9]{32}$")


def _session_dir(session_id: str) -> Path:
    if not _SAFE_ID.match(session_id):
        raise HTTPException(status_code=404, detail="Unknown report")
    path = DATA_DIR / session_id
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Unknown or expired report")
    return path


def _parse_seats(raw: str | None) -> tuple[str, ...]:
    if not raw or not raw.strip():
        return DEFAULT_SEAT_EMAILS
    seats = [s.strip() for s in re.split(r"[\s,;]+", raw) if s.strip() and "@" in s]
    return tuple(seats) if seats else DEFAULT_SEAT_EMAILS


def _inject_web_chrome(html: str, session_id: str, source_name: str) -> str:
    """Add sticky toolbar: new upload + copy AI summary."""
    bar = f"""
<div id="web-toolbar" class="web-toolbar">
  <div class="web-toolbar-inner">
    <a class="web-btn secondary" href="/">← Upload new CSV</a>
    <span class="web-toolbar-label">Source: <code>{source_name}</code></span>
    <button type="button" class="web-btn" id="copy-ai-summary" data-url="/r/{session_id}/summary.txt">
      Copy AI analysis pack
    </button>
    <a class="web-btn secondary" href="/r/{session_id}/summary.txt" target="_blank" rel="noopener">Open as text</a>
    <span id="copy-status" class="web-status" aria-live="polite"></span>
  </div>
</div>
<style>
  .web-toolbar {{
    position: sticky; top: 0; z-index: 50;
    background: rgba(11, 58, 91, 0.96); color: #fff;
    box-shadow: 0 4px 16px rgba(11, 58, 91, 0.25);
  }}
  .web-toolbar-inner {{
    max-width: 1180px; margin: 0 auto;
    padding: 10px 24px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center;
    font-family: "IBM Plex Sans", Segoe UI, system-ui, sans-serif; font-size: 0.9rem;
  }}
  .web-toolbar-label {{ flex: 1; min-width: 140px; opacity: 0.9; font-size: 12px; }}
  .web-toolbar-label code {{
    font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 11px;
    background: rgba(255,255,255,0.12); padding: 2px 6px; border-radius: 4px;
  }}
  .web-btn {{
    appearance: none; border: none; border-radius: 8px; cursor: pointer;
    padding: 8px 12px; font: inherit; font-weight: 600; font-size: 0.88rem;
    background: #C45C26; color: #fff; text-decoration: none; display: inline-block;
  }}
  .web-btn:hover {{ filter: brightness(1.06); }}
  .web-btn.secondary {{ background: rgba(255,255,255,0.14); color: #fff; }}
  .web-status {{ font-size: 12px; opacity: 0.95; min-width: 4ch; }}
  body {{ padding-top: 0; }}
  .tabs {{ top: 52px !important; }}
</style>
<script>
  (function () {{
    const btn = document.getElementById('copy-ai-summary');
    const status = document.getElementById('copy-status');
    if (!btn) return;
    btn.addEventListener('click', async () => {{
      status.textContent = 'Loading…';
      try {{
        const res = await fetch(btn.dataset.url);
        if (!res.ok) throw new Error('fetch failed');
        const text = await res.text();
        await navigator.clipboard.writeText(text);
        status.textContent = 'Copied — paste into your AI chat';
        setTimeout(() => {{ status.textContent = ''; }}, 4000);
      }} catch (e) {{
        status.textContent = 'Copy failed — use Open as text';
      }}
    }});
  }})();
</script>
"""
    if "<body>" in html:
        return html.replace("<body>", "<body>\n" + bar, 1)
    return bar + html


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "default_seat_cost": DEFAULT_SEAT_COST_USD,
            "default_seats": "\n".join(DEFAULT_SEAT_EMAILS),
            "error": None,
        },
    )


@app.post("/upload", response_model=None)
async def upload(
    request: Request,
    file: UploadFile = File(...),
    seat_cost: float = Form(DEFAULT_SEAT_COST_USD),
    seats: str = Form(""),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "default_seat_cost": seat_cost,
                "default_seats": seats or "\n".join(DEFAULT_SEAT_EMAILS),
                "error": "Please upload a Cursor team usage CSV (team-usage-events-*.csv).",
            },
            status_code=400,
        )

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large")
    if not raw.strip():
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "default_seat_cost": seat_cost,
                "default_seats": seats or "\n".join(DEFAULT_SEAT_EMAILS),
                "error": "Uploaded file was empty.",
            },
            status_code=400,
        )

    session_id = uuid.uuid4().hex
    session_path = DATA_DIR / session_id
    session_path.mkdir(parents=True, exist_ok=True)
    safe_name = Path(file.filename).name
    csv_path = session_path / safe_name
    csv_path.write_bytes(raw)

    seat_emails = _parse_seats(seats)
    try:
        df = load_usage(csv_path)
        html = build_report(df, safe_name, seat_emails=seat_emails, seat_cost_usd=seat_cost)
        html = _inject_web_chrome(html, session_id, safe_name)
        (session_path / "report.html").write_text(html, encoding="utf-8")
        summary = build_ai_summary(df, safe_name, seat_emails=seat_emails, seat_cost_usd=seat_cost)
        (session_path / "summary.md").write_text(summary, encoding="utf-8")
        meta = account_spend(df, seat_emails=seat_emails, seat_cost_usd=seat_cost)
        (session_path / "meta.txt").write_text(
            f"events={len(df)} total_usd={meta['total_usd']:.2f}\n",
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001 — surface parse errors to the upload form
        shutil.rmtree(session_path, ignore_errors=True)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "default_seat_cost": seat_cost,
                "default_seats": seats or "\n".join(DEFAULT_SEAT_EMAILS),
                "error": f"Could not parse CSV: {exc}",
            },
            status_code=400,
        )

    return RedirectResponse(url=f"/r/{session_id}/", status_code=303)


@app.get("/r/{session_id}/", response_class=HTMLResponse)
async def view_report(session_id: str) -> HTMLResponse:
    path = _session_dir(session_id) / "report.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Report not found")
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/r/{session_id}/summary.txt", response_class=PlainTextResponse)
async def view_summary(session_id: str) -> PlainTextResponse:
    path = _session_dir(session_id) / "summary.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Summary not found")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=DEFAULT_PORT, reload=False)
