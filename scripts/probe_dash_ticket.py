"""
One-off discovery: fetch one Copilot Dash ticket detail JSON and print
where "Resolved Model Name" lives. Used to wire up dash_ocv_extract.py.

Usage:
  python scripts/probe_dash_ticket.py <ticketId>

Prints the full key tree (depths<=4) plus any keys whose name contains
"model" / "resolved" along with sample values. Saves the raw JSON to
data/.dash_probe_<ticketId>.json for inspection.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
PROFILE_DIR = PROJECT_ROOT / ".browser-profile-dash"
DATA_DIR = PROJECT_ROOT / "data"
TICKET_BASE = "https://copilotdash.microsoft.com/ticket/"
TICKET_API_FRAGMENT = "/api/v2/tickets/"


def walk_keys(obj, prefix="", depth=0, max_depth=5, out=None):
    if out is None:
        out = []
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            t = type(v).__name__
            if isinstance(v, (dict, list)):
                out.append((path, t, None))
                walk_keys(v, path, depth + 1, max_depth, out)
            else:
                sample = str(v)
                if len(sample) > 80:
                    sample = sample[:77] + "..."
                out.append((path, t, sample))
    elif isinstance(obj, list):
        if obj:
            walk_keys(obj[0], f"{prefix}[0]", depth + 1, max_depth, out)
            out.append((f"{prefix}[len]", "int", str(len(obj))))
    return out


async def main(tid: str) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=False,
            viewport={"width": 1400, "height": 900},
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

        page.on("request", lambda r: asyncio.create_task(on_request(r)))

        print(f"Opening {TICKET_BASE}{tid} (Edge profile reused)...", flush=True)
        await page.goto(TICKET_BASE + tid, wait_until="domcontentloaded")

        for _ in range(60):
            if bearer["v"]:
                break
            await asyncio.sleep(1)
        if not bearer["v"]:
            print("ERROR: Never captured bearer token. Is the page loaded / logged in?", flush=True)
            await ctx.close()
            return 2

        request_ctx = await p.request.new_context(
            base_url="https://copilotdash.microsoft.com",
            extra_http_headers={
                "Authorization": bearer["v"],
                "Accept": "application/json",
                "Origin": "https://copilotdash.microsoft.com",
                "Referer": TICKET_BASE + tid,
            },
            storage_state=await ctx.storage_state(),
        )

        resp = await request_ctx.get(f"/api/v2/tickets/{tid}")
        print(f"HTTP {resp.status}", flush=True)
        if not resp.ok:
            print(await resp.text(), flush=True)
            await ctx.close()
            return 3

        data = await resp.json()

        # Save full raw response (will be deleted after analysis)
        out_path = DATA_DIR / f".dash_probe_{tid}.json"
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nFull JSON saved to: {out_path}", flush=True)

        # Walk keys
        tree = walk_keys(data, max_depth=6)

        # Highlight anything related to model / resolved / path
        print("\n=== Keys mentioning model / resolv / harness / path / branch ===", flush=True)
        for path, t, sample in tree:
            low = path.lower()
            if any(s in low for s in ("model", "resolv", "harness", "branch", "path")):
                print(f"  {path}  ({t})  {sample or ''}", flush=True)

        print("\n=== Top-level keys ===", flush=True)
        for k, v in (data or {}).items():
            t = type(v).__name__
            extra = f"  len={len(v)}" if isinstance(v, (list, dict, str)) else ""
            print(f"  {k}  ({t}){extra}", flush=True)

        # Also see if any of the chat items have keys we don't know about
        dc = data.get("diagnosticContext") or {}
        chat = dc.get("chat") or []
        print(f"\n=== diagnosticContext.chat has {len(chat)} turn(s) ===", flush=True)
        if chat:
            print("Keys on first turn:", flush=True)
            for k in chat[0].keys():
                v = chat[0][k]
                t = type(v).__name__
                sample = str(v)[:120] if not isinstance(v, (list, dict)) else f"[{type(v).__name__}, len={len(v)}]"
                print(f"  {k}  ({t})  {sample}", flush=True)

        await ctx.close()
        return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    raise SystemExit(asyncio.run(main(sys.argv[1])))
