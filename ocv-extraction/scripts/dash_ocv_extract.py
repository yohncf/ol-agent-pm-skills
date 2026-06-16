"""
Copilot Dash → OCV ticket extractor.

Walks the Copilot Dash feedback table (https://copilotdash.microsoft.com), captures
ticket IDs from /api/v2/tickets/search API responses while auto-scrolling the
virtualized list, filters by a date range, and then fetches per-ticket details
(/api/v2/tickets/{id}) to extract the OCV link + utterance + verbatim + response.

Output: CSV with columns
  Dash ticket | OCV ticket | Utterance | Verbatim | Response | Resolved Models | Path

Usage examples:
  python scripts/dash_ocv_extract.py --date yesterday
  python scripts/dash_ocv_extract.py --date 7d
  python scripts/dash_ocv_extract.py --from 2026-05-11 --to 2026-05-21
  python scripts/dash_ocv_extract.py --from 2026-05-15
  python scripts/dash_ocv_extract.py --date 2026-05-18  # single day
  python scripts/dash_ocv_extract.py --url "https://copilotdash.microsoft.com/product/feedback?..." \
      --from 2026-05-11 --to 2026-05-21 --out data/dash_ocv_custom.csv

Requirements:
  pip install playwright
  python -m playwright install chromium  # Edge channel is auto-detected via channel='msedge'

The browser profile is persisted at ocv-extraction/.browser-profile-dash so you only
sign in once.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

# Make scripts/lib importable when this script runs as `python scripts/dash_ocv_extract.py`
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared"))
from lib.model_path import diagnose_path, path_slug  # noqa: E402

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.stderr.write(
        "ERROR: playwright is not installed.\n"
        "Run: pip install playwright && python -m playwright install chromium\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Paths and defaults
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PROFILE_DIR = PROJECT_ROOT / ".browser-profile-dash"
DATA_DIR = PROJECT_ROOT / "data"

DEFAULT_URL = (
    "https://copilotdash.microsoft.com/product/feedback"
    "?product=M365Chat&applications=&userId=&thumbs=Negative"
    "&tab=allFeedback&copExtIds=OutlookAIAgent"
)
DASH_TICKET_BASE = "https://copilotdash.microsoft.com/ticket/"
SEARCH_API_FRAGMENT = "/api/v2/tickets/search"
TICKET_API_FRAGMENT = "/api/v2/tickets/"


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
@dataclass
class DateRange:
    start: datetime  # inclusive, UTC
    end: datetime    # exclusive, UTC (start of day after the user's end date)
    label: str       # used in the output filename


def _utc_midnight(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def resolve_date_range(args: argparse.Namespace) -> DateRange:
    """Resolve --date preset OR --from/--to into a DateRange."""
    today = datetime.now(timezone.utc).date()

    if args.from_ or args.to:
        start_d = _parse_iso_date(args.from_) if args.from_ else today
        end_d = _parse_iso_date(args.to) if args.to else today
        if start_d > end_d:
            raise SystemExit(f"--from ({start_d}) must be <= --to ({end_d})")
        label = f"{start_d.isoformat()}" if start_d == end_d else f"{start_d.isoformat()}_to_{end_d.isoformat()}"
        return DateRange(_utc_midnight(start_d), _utc_midnight(end_d) + timedelta(days=1), label)

    preset = (args.date or "yesterday").strip().lower()

    # Allow a YYYY-MM-DD as a single-day preset
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", preset):
        d = _parse_iso_date(preset)
        return DateRange(_utc_midnight(d), _utc_midnight(d) + timedelta(days=1), d.isoformat())

    if preset == "today":
        return DateRange(_utc_midnight(today), _utc_midnight(today) + timedelta(days=1), today.isoformat())

    if preset == "yesterday":
        y = today - timedelta(days=1)
        return DateRange(_utc_midnight(y), _utc_midnight(y) + timedelta(days=1), y.isoformat())

    m = re.fullmatch(r"(\d+)\s*([dwm])", preset)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "d":
            delta = timedelta(days=n)
        elif unit == "w":
            delta = timedelta(weeks=n)
        else:  # 'm' = months, approximate as 30 days
            delta = timedelta(days=30 * n)
        start_d = (today - delta) + timedelta(days=1)  # rolling window ending today inclusive
        return DateRange(_utc_midnight(start_d), _utc_midnight(today) + timedelta(days=1), f"{start_d.isoformat()}_to_{today.isoformat()}")

    # Range form: YYYY-MM-DD:YYYY-MM-DD
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})", preset)
    if m:
        a, b = _parse_iso_date(m.group(1)), _parse_iso_date(m.group(2))
        return DateRange(_utc_midnight(a), _utc_midnight(b) + timedelta(days=1), f"{a.isoformat()}_to_{b.isoformat()}")

    raise SystemExit(
        f"Unrecognized --date value: {preset!r}. Try: today, yesterday, 7d, 14d, 30d, 3m, "
        "a single YYYY-MM-DD, or a range like 2026-05-11:2026-05-21."
    )


# ---------------------------------------------------------------------------
# Listing scrape: capture ticket IDs from /api/v2/tickets/search
# ---------------------------------------------------------------------------
ITEM_RE = re.compile(
    r'"id":"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"'
    r'(?:(?!"id":"[0-9a-f]{8}-).)*?"createDateTime":"(?P<dt>[^"]+)"',
    re.DOTALL,
)


def parse_items_from_search_body(body: str) -> list[tuple[str, datetime]]:
    """Pull (id, createDateTime) tuples from a /api/v2/tickets/search response body."""
    out: list[tuple[str, datetime]] = []
    for m in ITEM_RE.finditer(body):
        try:
            dt = datetime.fromisoformat(m.group("dt").replace("Z", "+00:00"))
        except ValueError:
            continue
        out.append((m.group("id"), dt))
    return out


SCROLL_SCRIPT = r"""
async (cutoffEpochMs) => {
    const candidates = Array.from(document.querySelectorAll('*')).filter(el => {
        const cs = getComputedStyle(el);
        return (cs.overflowY === 'auto' || cs.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 50
            && el.clientHeight > 200;
    });
    candidates.sort((a, b) => (b.scrollHeight * b.clientHeight) - (a.scrollHeight * a.clientHeight));
    const target = candidates[0];
    if (!target) return { ok: false, reason: 'no_scroll_target' };

    // Look at any visible MM/DD prefixes in the table body; convert to a comparable date
    // assuming the year is the current UTC year (the dashboard hides year, but new
    // entries arrive newest-first so we just need to know when we've scrolled past cutoff).
    const currentYear = new Date().getUTCFullYear();
    function oldestVisibleEpoch() {
        const texts = Array.from(document.querySelectorAll('[role="row"]')).map(r => r.innerText.slice(0, 40));
        let oldest = Infinity;
        for (const t of texts) {
            const m = t.match(/^(\d{2})\/(\d{2})\b/);
            if (!m) continue;
            const month = parseInt(m[1], 10) - 1;
            const day = parseInt(m[2], 10);
            const epoch = Date.UTC(currentYear, month, day);
            if (epoch < oldest) oldest = epoch;
        }
        return oldest;
    }

    let lastTop = -1;
    let stable = 0;
    let iterations = 0;
    while (iterations < 600 && stable < 6) {
        target.scrollTop = target.scrollTop + target.clientHeight * 0.9;
        await new Promise(r => setTimeout(r, 650));
        iterations++;
        const oldest = oldestVisibleEpoch();
        if (oldest !== Infinity && oldest < cutoffEpochMs) {
            // Scroll a little further so the cutoff day itself is fully loaded
            for (let i = 0; i < 4; i++) {
                target.scrollTop = target.scrollTop + target.clientHeight * 0.5;
                await new Promise(r => setTimeout(r, 600));
            }
            return { ok: true, iterations, scrollTop: target.scrollTop, reachedCutoff: true, oldestEpoch: oldest };
        }
        if (target.scrollTop === lastTop) stable++; else stable = 0;
        lastTop = target.scrollTop;
    }
    return { ok: true, iterations, scrollTop: target.scrollTop, reachedCutoff: false, oldestEpoch: oldestVisibleEpoch() };
}
"""


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------
async def run(args: argparse.Namespace) -> int:
    drange = resolve_date_range(args)
    print(f"Date range (UTC): {drange.start.isoformat()}  ..  {drange.end.isoformat()} (exclusive)", flush=True)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    out_csv = Path(args.out) if args.out else DATA_DIR / f"dash_ocv_{drange.label}.csv"
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    print(f"Output CSV: {out_csv}", flush=True)

    listing_url = args.url or DEFAULT_URL
    print(f"Dashboard URL: {listing_url}", flush=True)

    captured_items: dict[str, datetime] = {}

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=False,
            viewport={"width": 1600, "height": 1000},
            args=["--start-maximized"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        bearer = {"v": None}

        async def on_request(req):
            if TICKET_API_FRAGMENT in req.url and not bearer["v"]:
                try:
                    h = await req.all_headers()
                    a = h.get("authorization") or h.get("Authorization")
                    if a and a.lower().startswith("bearer "):
                        bearer["v"] = a
                except Exception:
                    pass

        async def on_response(resp):
            try:
                if SEARCH_API_FRAGMENT not in resp.url:
                    return
                ct = (resp.headers or {}).get("content-type", "")
                if "json" not in ct.lower():
                    return
                body = await resp.text()
                for tid, dt in parse_items_from_search_body(body):
                    # Keep the earliest seen createDateTime (they should be identical anyway)
                    prev = captured_items.get(tid)
                    if prev is None or dt < prev:
                        captured_items[tid] = dt
            except Exception:
                pass

        page.on("request", lambda r: asyncio.create_task(on_request(r)))
        page.on("response", lambda r: asyncio.create_task(on_response(r)))

        print(f"\nOpening Copilot Dash listing page...", flush=True)
        await page.goto(listing_url, wait_until="domcontentloaded")

        print("", flush=True)
        print("=" * 72, flush=True)
        print("ACTION REQUIRED in the Edge window:", flush=True)
        print("  1) Sign in if prompted.", flush=True)
        print("  2) Wait until the feedback table renders (rows visible).", flush=True)
        print("  3) RECOMMENDED: open the column picker and add the 'Ticket ID'", flush=True)
        print("     (and 'OCV ticket' / 'OCV Link' if available) column so you can", flush=True)
        print("     visually verify the data while the script scrolls.", flush=True)
        print("  4) Optionally pre-scroll the table to load older rows.", flush=True)
        print("", flush=True)
        print("  WHEN READY: create a sentinel file at the repo root:", flush=True)
        print("      PowerShell:  New-Item .dash_ready -ItemType File -Force", flush=True)
        print("      bash/zsh:    touch .dash_ready", flush=True)
        print("  The script will then take over and auto-scroll/capture.", flush=True)
        print("  (Up to 30 minutes \u2014 take your time.)", flush=True)
        print("=" * 72, flush=True)
        loop = asyncio.get_event_loop()
        await _wait_for_user_or_table(page, loop, captured_items, timeout_s=1800)

        # Auto-scroll until we see a row dated before drange.start.
        # Wrap in a timeout so a hung page can't block forever — if the page
        # is frozen, we still proceed with whatever search responses were
        # captured before the auto-scroll attempt.
        cutoff_ms = int(drange.start.timestamp() * 1000)
        print(f"\nAuto-scrolling until rows older than {drange.start.date().isoformat()} are visible...", flush=True)
        try:
            scroll_result = await asyncio.wait_for(
                page.evaluate(SCROLL_SCRIPT, cutoff_ms),
                timeout=300,
            )
            print(f"Scroll result: {scroll_result}", flush=True)
        except asyncio.TimeoutError:
            print(
                "Auto-scroll timed out after 300s (page likely hung). "
                f"Proceeding with {len(captured_items)} tickets captured so far.",
                flush=True,
            )
        except Exception as exc:
            print(
                f"Auto-scroll raised {type(exc).__name__}: {exc}. "
                f"Proceeding with {len(captured_items)} tickets captured so far.",
                flush=True,
            )

        # Let any in-flight requests finish
        await asyncio.sleep(3)
        print(f"Captured {len(captured_items)} unique ticket IDs across all search responses.", flush=True)

        # Filter by date range
        in_range = {tid: dt for tid, dt in captured_items.items() if drange.start <= dt < drange.end}
        # Sort newest first
        ordered = sorted(in_range.items(), key=lambda kv: kv[1], reverse=True)
        print(f"Tickets within date range: {len(ordered)}", flush=True)

        if not ordered:
            print("Nothing to fetch. Did the dashboard return results for this range?", flush=True)
            print("Press Enter to close the browser (or it will close in 30s).", flush=True)
            await _readline_with_timeout(loop, 30)
            await ctx.close()
            return 1

        # Make sure we have a bearer token (a /api/v2/tickets request must have fired)
        if not bearer["v"]:
            print("\nNo bearer token captured yet. Opening the newest ticket once to seed it...", flush=True)
            await page.goto(DASH_TICKET_BASE + ordered[0][0], wait_until="domcontentloaded")
            for _ in range(30):
                if bearer["v"]:
                    break
                await asyncio.sleep(1)
        if not bearer["v"]:
            print("ERROR: Never captured the Authorization bearer token. Aborting.", flush=True)
            await ctx.close()
            return 2

        # Build APIRequestContext that reuses cookies + bearer header
        request_ctx = await p.request.new_context(
            base_url="https://copilotdash.microsoft.com",
            extra_http_headers={
                "Authorization": bearer["v"],
                "Accept": "application/json",
                "Origin": "https://copilotdash.microsoft.com",
                "Referer": DASH_TICKET_BASE + ordered[0][0],
            },
            storage_state=await ctx.storage_state(),
        )

        rows: list[dict] = []
        save_every = 25
        total = len(ordered)

        async def reseed_bearer(referrer_tid: str):
            nonlocal request_ctx
            bearer["v"] = None
            await page.goto(DASH_TICKET_BASE + referrer_tid, wait_until="domcontentloaded")
            for _ in range(30):
                if bearer["v"]:
                    break
                await asyncio.sleep(1)
            if bearer["v"]:
                await request_ctx.dispose()
                request_ctx = await p.request.new_context(
                    base_url="https://copilotdash.microsoft.com",
                    extra_http_headers={
                        "Authorization": bearer["v"],
                        "Accept": "application/json",
                        "Origin": "https://copilotdash.microsoft.com",
                        "Referer": DASH_TICKET_BASE + referrer_tid,
                    },
                    storage_state=await ctx.storage_state(),
                )

        def text_of(v):
            if isinstance(v, dict):
                return v.get("text", "") or ""
            return v or ""

        for i, (tid, dt) in enumerate(ordered, 1):
            try:
                resp = await request_ctx.get(f"/api/v2/tickets/{tid}")
                if resp.status in (401, 403):
                    print(f"  [{i}/{total}] {tid} HTTP {resp.status} -- reseeding token", flush=True)
                    await reseed_bearer(tid)
                    resp = await request_ctx.get(f"/api/v2/tickets/{tid}")
                if not resp.ok:
                    print(f"  [{i}/{total}] {tid} HTTP {resp.status}", flush=True)
                    rows.append({"id": tid, "createDateTime": dt.isoformat(), "error": f"HTTP {resp.status}"})
                    continue
                data = await resp.json()
                uf = data.get("userFeedback") or {}
                dc = data.get("diagnosticContext") or {}
                chat = dc.get("chat") or []
                first = chat[0] if chat else {}
                ocv = uf.get("ocvLink", "") or ""
                verb = text_of(uf.get("verbatim"))
                utt = text_of(first.get("utterance"))
                rsp = text_of(first.get("response"))
                resolved_models = [m for m in (dc.get("resolvedModelName") or []) if m]
                branch, _disp, _rule, _conf = diagnose_path(resolved_models)
                rows.append({
                    "id": tid,
                    "createDateTime": dt.isoformat(),
                    "ocvLink": ocv,
                    "utterance": utt,
                    "verbatim": verb,
                    "response": rsp,
                    "resolvedModels": resolved_models,
                    "path": path_slug(branch),
                })
                print(
                    f"  [{i}/{total}] {tid} ocv={'Y' if ocv else 'N'} utt={len(utt)}c "
                    f"resp={len(rsp)}c models={len(resolved_models)} path={path_slug(branch)}",
                    flush=True,
                )
            except Exception as e:
                print(f"  [{i}/{total}] {tid} ERROR: {e}", flush=True)
                rows.append({"id": tid, "createDateTime": dt.isoformat(), "error": str(e)})

            if i % save_every == 0:
                _write_progress(out_csv.with_suffix(".progress.json"), rows)

        _write_progress(out_csv.with_suffix(".progress.json"), rows)

        # Build the final CSV
        with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Dash ticket", "OCV ticket", "Utterance", "Verbatim", "Response",
                        "Resolved Models", "Path"])
            for r in rows:
                w.writerow([
                    DASH_TICKET_BASE + r["id"],
                    r.get("ocvLink", ""),
                    r.get("utterance", ""),
                    r.get("verbatim", ""),
                    r.get("response", ""),
                    ";".join(r.get("resolvedModels", []) or []),
                    r.get("path", ""),
                ])

        ok = sum(1 for r in rows if "error" not in r)
        ocv_present = sum(1 for r in rows if r.get("ocvLink"))
        print("", flush=True)
        print(f"DONE. Wrote {out_csv}", flush=True)
        print(f"  Rows: {len(rows)}  (success: {ok}, with OCV: {ocv_present})", flush=True)

        if not args.keep_open:
            await request_ctx.dispose()
            await ctx.close()
        else:
            print("\n--keep-open: leaving the browser open. Press Enter to close.", flush=True)
            await loop.run_in_executor(None, sys.stdin.readline)
            await request_ctx.dispose()
            await ctx.close()

    return 0


def _write_progress(path: Path, rows: list[dict]) -> None:
    try:
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  (progress save failed: {e})", flush=True)


async def _readline_with_timeout(loop, timeout_s: float) -> None:
    """Wait for an Enter keypress on stdin, up to `timeout_s` seconds.

    Safe to call when stdin is not a TTY (e.g., the script is invoked via a
    subprocess pipe): in that case readline() returns EOF immediately and the
    function returns without raising.
    """
    try:
        await asyncio.wait_for(
            loop.run_in_executor(None, sys.stdin.readline),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        pass


SENTINEL_PATH = Path(__file__).resolve().parent.parent / ".dash_ready"


async def _wait_for_user_or_table(page, loop, captured_items: dict, timeout_s: float = 1800) -> None:
    """Block until either:
       - a sentinel file at SENTINEL_PATH appears (user signals "I'm ready"), or
       - the timeout elapses (default: 30 min, generous for sign-in + column
         setup + manual scroll of large date ranges).

    The sentinel-file approach is reliable from both interactive shells and
    orchestrator subprocesses, and gives the user unlimited time to sign in,
    configure dashboard columns, and pre-scroll the virtualized list before
    the script takes over. Stale sentinels from prior runs are cleared.
    """
    # Clear any stale sentinel from a prior run.
    try:
        if SENTINEL_PATH.exists():
            SENTINEL_PATH.unlink()
    except Exception:
        pass

    print(
        f"  Waiting for sentinel file at: {SENTINEL_PATH}",
        flush=True,
    )
    print(
        "  (Create the file when you've finished sign-in / column setup / "
        "manual scrolling, e.g.: `New-Item .dash_ready` or `touch .dash_ready`)",
        flush=True,
    )

    async def _safe_row_count() -> int:
        try:
            return await asyncio.wait_for(
                page.evaluate("document.querySelectorAll('[role=\"row\"]').length"),
                timeout=3,
            )
        except Exception:
            return -1

    deadline = asyncio.get_event_loop().time() + timeout_s
    last_status_log = 0.0
    while asyncio.get_event_loop().time() < deadline:
        # Check sentinel FIRST (cheap, never blocks). Do not gate on page.evaluate
        # because the page may be mid-navigation / SSO redirect / dialog and
        # evaluate() can stall indefinitely.
        if SENTINEL_PATH.exists():
            row_count = await _safe_row_count()
            row_str = str(row_count) if row_count >= 0 else "n/a"
            print(
                f"  Sentinel detected. Captured={len(captured_items)} ticket IDs, "
                f"visible rows={row_str}. Proceeding.",
                flush=True,
            )
            try:
                SENTINEL_PATH.unlink()
            except Exception:
                pass
            await asyncio.sleep(2)
            return

        now = asyncio.get_event_loop().time()
        if now - last_status_log > 30:
            row_count = await _safe_row_count()
            row_str = str(row_count) if row_count >= 0 else "n/a"
            print(
                f"  Waiting for sentinel\u2026 captured={len(captured_items)} "
                f"ticket IDs, visible rows={row_str}",
                flush=True,
            )
            last_status_log = now
        await asyncio.sleep(2)

    print(
        f"  Timeout after {timeout_s}s waiting for sentinel file. Proceeding anyway.",
        flush=True,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Extract Copilot Dash tickets + OCV link + verbatim + utterance + response "
            "into a CSV."
        )
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--date",
        help=(
            "Preset window: today, yesterday, 7d, 14d, 30d, 3m, a single YYYY-MM-DD, "
            "or a range YYYY-MM-DD:YYYY-MM-DD. Defaults to 'yesterday'."
        ),
    )
    p.add_argument("--from", dest="from_", metavar="YYYY-MM-DD", help="Start date (UTC, inclusive). Use with --to.")
    p.add_argument("--to", metavar="YYYY-MM-DD", help="End date (UTC, inclusive). Use with --from.")
    p.add_argument(
        "--url",
        help=(
            "Override the Copilot Dash listing URL. Defaults to the M365Chat + "
            "Negative + OutlookAIAgent feedback view."
        ),
    )
    p.add_argument("--out", help="Output CSV path. Defaults to data/dash_ocv_<range>.csv.")
    p.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave the Edge window open after finishing (handy for spot-checking).",
    )
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    if (args.from_ or args.to) and args.date:
        raise SystemExit("Use either --date OR --from/--to, not both.")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted.", flush=True)
        return 130


if __name__ == "__main__":
    sys.exit(main())
