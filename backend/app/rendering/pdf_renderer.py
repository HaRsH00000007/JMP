"""HTML → PDF with Playwright Chromium (generation-flow.md §3).

* one long-lived browser per process, owned by a single renderer thread (the sync API is not thread-safe),
  a fresh context per job, browser restarted after N renders or on crash
* the page is loaded from a temporary file so embedded fonts resolve locally (no network)
* overflow check: any `.page` whose content exceeds A4, or any element clipped past the page bottom,
  fails the render with RENDER_OVERFLOW — content is never silently cut
* PDF metadata carries every version stamp
"""

from __future__ import annotations

import io
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from app.errors import ErrorCode, RenderError
from app.observability.logging import get_logger
from app.settings import settings

log = get_logger("jmp.pdf")
_local = threading.local()
_MAX_RENDERS_PER_BROWSER = 200

OVERFLOW_JS = """
() => {
  const out = [];
  document.querySelectorAll('section.page').forEach((pg) => {
    const pr = pg.getBoundingClientRect();
    const n = pg.getAttribute('data-page');
    if (pg.scrollHeight > pg.clientHeight + 1) out.push({page: n, reason: 'page content taller than A4',
        scroll: pg.scrollHeight, client: pg.clientHeight});
    const limit = pr.bottom - 1;
    pg.querySelectorAll('table, .tiles, .h2, p, .recs, .final, .reco, .exp, .decision, .footer, svg, .alts, .verify, .wide-band')
      .forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.height > 0 && r.bottom > limit) out.push({page: n, reason: 'element clipped at page bottom',
            element: el.tagName.toLowerCase() + '.' + (el.className && el.className.baseVal === undefined ? el.className : ''),
            bottom: Math.round(r.bottom - pr.top)});
      });
  });
  return out;
}
"""


# A page whose prose runs a few lines long (real narrative length varies) is scaled down in 1% steps, to at
# most 90%, before the overflow check. Nothing is clipped: a page that still does not fit fails as before.
FIT_JS = """
() => {
  const scaled = [];
  document.querySelectorAll('section.page').forEach((pg) => {
    if (pg.scrollHeight <= pg.clientHeight + 1) return;
    const kids = [...pg.children].filter((k) => !k.classList.contains('footer'));
    let z = 1.0;
    while (pg.scrollHeight > pg.clientHeight + 1 && z > 0.905) {
      z = Math.round((z - 0.01) * 100) / 100;
      kids.forEach((k) => { k.style.zoom = z; });
    }
    scaled.push({page: pg.getAttribute('data-page'), zoom: z});
  });
  return scaled;
}
"""


@dataclass
class PdfResult:
    pdf: bytes
    page_count: int
    render_ms: int


def _browser():  # noqa: ANN202
    from playwright.sync_api import sync_playwright

    state = getattr(_local, "state", None)
    if state is not None and state["count"] < _MAX_RENDERS_PER_BROWSER and state["browser"].is_connected():
        return state
    close_browser()
    pw = sync_playwright().start()
    browser = pw.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"])
    _local.state = {"pw": pw, "browser": browser, "count": 0}
    return _local.state


def close_browser() -> None:
    state = getattr(_local, "state", None)
    if state is None:
        return
    try:
        state["browser"].close()
        state["pw"].stop()
    except Exception:  # noqa: BLE001 - best-effort shutdown
        pass
    _local.state = None


_render_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def render_pdf(html: str, *, metadata: dict[str, str], expected_pages: int = 8) -> PdfResult:
    """All renders in a process go through ONE dedicated renderer thread that owns the long-lived Chromium.
    This keeps one browser per process (not per request thread) and is safe inside asyncio contexts, where
    Playwright's sync API cannot run. Horizontal scaling = more render worker processes."""
    global _render_executor
    with _executor_lock:
        if _render_executor is None:
            _render_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jmp-pdf")
    return _render_executor.submit(_render_pdf, html, metadata=metadata, expected_pages=expected_pages).result()


def shutdown_renderer() -> None:
    global _render_executor
    with _executor_lock:
        if _render_executor is not None:
            _render_executor.submit(close_browser).result()
            _render_executor.shutdown(wait=True)
            _render_executor = None


def _render_pdf(html: str, *, metadata: dict[str, str], expected_pages: int = 8) -> PdfResult:
    t0 = time.monotonic()
    timeout = settings().pdf_render_timeout_ms
    with tempfile.TemporaryDirectory(prefix="jmp-render-") as tmp:
        path = Path(tmp) / "report.html"
        path.write_text(html, encoding="utf-8")
        for attempt in (1, 2):
            state = _browser()
            context = state["browser"].new_context()
            try:
                page = context.new_page()
                page.goto(path.as_uri(), wait_until="load", timeout=timeout)
                page.emulate_media(media="print")
                page.evaluate("document.fonts.ready")
                scaled = page.evaluate(FIT_JS)
                if scaled:
                    log.info("pdf_pages_scaled_to_fit", pages=scaled)
                problems = page.evaluate(OVERFLOW_JS)
                if problems:
                    raise RenderError("Report content overflows the fixed A4 layout", code=ErrorCode.RENDER_OVERFLOW,
                                      details=problems[:10])
                pdf = page.pdf(format="A4", print_background=True, prefer_css_page_size=True,
                               margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
                state["count"] += 1
                break
            except RenderError:
                raise
            except Exception as exc:  # browser crash etc. -> restart once, then retryable failure
                log.warning("pdf_render_retry", attempt=attempt, error=type(exc).__name__)
                close_browser()
                if attempt == 2:
                    raise RenderError(f"Chromium render failed: {type(exc).__name__}",
                                      code=ErrorCode.PDF_RENDER_FAILED) from exc
            finally:
                try:
                    context.close()
                except Exception:  # noqa: BLE001
                    pass
    reader = PdfReader(io.BytesIO(pdf))
    writer = PdfWriter(clone_from=reader)
    writer.add_metadata({f"/{k}": v for k, v in metadata.items()})
    buf = io.BytesIO()
    writer.write(buf)
    n = len(reader.pages)
    if n != expected_pages:
        raise RenderError(f"PDF has {n} pages; the fixed template requires {expected_pages}",
                          code=ErrorCode.RENDER_OVERFLOW)
    return PdfResult(pdf=buf.getvalue(), page_count=n, render_ms=int((time.monotonic() - t0) * 1000))
