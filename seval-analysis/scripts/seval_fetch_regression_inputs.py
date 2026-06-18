#!/usr/bin/env python
"""
Fetch SEVAL regression inputs from a single descriptor JSON.

Given a descriptor like:

    {
      "sevals": [
        { "date": "06/11/26", "queryset": "Hero V2.7",
          "control": "Mainline", "experiment": "CodeGen",
          "url": "https://seval.microsoft.com/detail/559151" },
        { "date": "06/12/26", "queryset": "Hero V2.7",
          "control": "Mainline", "experiment": "CodeGen",
          "url": "https://seval.microsoft.com/detail/561048" }
      ]
    }

this script drives the SEVAL portal (Playwright + Edge, persistent profile so
SSO happens once) and, for EACH of the two runs, downloads:

  * the LM-checklist **Assertion view** results CSV
  * the **JSON Config** (SEVAL Settings JSON)

renaming both with the run id so the two runs never collide. Output:

    <out-dir>/<control-id>_assertions.csv
    <out-dir>/<control-id>_settings.json
    <out-dir>/<experiment-id>_assertions.csv
    <out-dir>/<experiment-id>_settings.json

Default out-dir: seval-input/regression_<control-id>_<experiment-id> (relative
to the repo root). The first entry in `sevals[]` is treated as the control
(baseline) run, the second as the experiment run.

Usage:
  python scripts/seval_fetch_regression_inputs.py --input "C:\\path\\regression.json"
  python scripts/seval_fetch_regression_inputs.py --input regression.json --out-dir D:\\some\\folder

First run opens a headed Edge window; complete Microsoft SSO if prompted.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent           # seval-analysis/
REPO_ROOT = PROJECT_ROOT.parent            # OLAgentWork/
PROFILE_DIR = PROJECT_ROOT / ".browser-profile-seval"


def log(msg: str) -> None:
    print(f"[seval-fetch] {msg}", flush=True)


def to_iso_date(raw: str) -> str:
    """Best-effort convert a descriptor date to YYYY-MM-DD (what the downstream
    eval_regression_extract.py requires). Accepts MM/DD/YY, MM/DD/YYYY, or an
    already-ISO string; returns the input unchanged if it can't be parsed."""
    if not raw:
        return ""
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", raw)
    if m:
        mo, da, yr = m.groups()
        yr = ("20" + yr) if len(yr) == 2 else yr
        return f"{yr}-{int(mo):02d}-{int(da):02d}"
    return raw


def run_id_from_url(url: str) -> str:
    """Parse the run id from a seval.microsoft.com /detail/<id> URL."""
    m = re.search(r"/detail/(\d+)", url)
    if not m:
        raise ValueError(f"could not parse a /detail/<id> run id from url: {url!r}")
    return m.group(1)


def wait_for_detail(page, login_timeout: int) -> bool:
    """Wait until the detail SPA has rendered its tablist."""
    deadline = time.time() + login_timeout
    while time.time() < deadline:
        try:
            if page.get_by_role("tab").count() > 0:
                return True
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return False


def open_tab(page, label_regex, appear_timeout: int = 90) -> bool:
    """Poll until a *real* tab (role=tab) matching label_regex mounts -- the
    SEVAL SPA lazy-loads some tabs (notably 'LM Checklist') several seconds
    after the initial tablist renders -- then click it.

    We deliberately do NOT fall back to get_by_text(): stray body text such as
    'LM checklist' matched non-tab nodes in earlier versions and mis-switched
    the panel, causing the wrong download to fire.
    """
    deadline = time.time() + appear_timeout
    last_dump = 0.0
    while time.time() < deadline:
        cand = page.get_by_role("tab", name=label_regex)
        if cand.count() > 0:
            cand.first.click()
            page.wait_for_timeout(3000)
            try:
                page.wait_for_load_state("networkidle", timeout=45000)
            except PWTimeout:
                pass
            return True
        if time.time() - last_dump > 20:
            last_dump = time.time()
            try:
                tabs = page.get_by_role("tab")
                names = [tabs.nth(i).inner_text().strip()[:40] for i in range(min(tabs.count(), 20))]
                log(f"    (waiting for tab; current tabs: {names})")
            except Exception:
                pass
        page.wait_for_timeout(2000)
    return False


def set_assertion_view(page) -> bool:
    """On the LM Checklist tab, switch the view dropdown (default 'Query View')
    to 'Assertion View'. The dropdown is the first Fluent dropdown button in the
    panel; the segment filter is a separate dropdown we leave untouched."""
    try:
        dd = page.locator(".fui-Dropdown__button").first
        if dd.count() == 0:
            return False
        current = (dd.inner_text() or "").strip()
        if re.search(r"Assertion\s*View", current, re.I):
            return True
        dd.click()
        page.wait_for_timeout(800)
        opt = page.get_by_role("option", name=re.compile(r"Assertion\s*View", re.I))
        if opt.count() == 0:
            page.keyboard.press("Escape")
            return False
        opt.first.click()
        page.wait_for_timeout(4000)
        return re.search(r"Assertion\s*View", (dd.inner_text() or ""), re.I) is not None
    except Exception as exc:
        log(f"  WARN: set_assertion_view failed: {exc}")
        return False


def _save_validated(dl, dest: Path, expect_ext: str) -> bool:
    """Save a Playwright download only if its suggested filename matches the
    expected extension; reject (and report) otherwise. This is the guard that
    prevents the JSON-config download from silently capturing the assertions
    CSV (whose 'Download' button persists in the DOM across tab switches)."""
    name = (dl.suggested_filename or "").lower()
    if expect_ext and not name.endswith(expect_ext):
        log(f"  reject: expected '*{expect_ext}' but got {dl.suggested_filename!r}")
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dl.save_as(str(dest))
    log(f"  saved {dest.name}  ({dest.stat().st_size:,} bytes)")
    return True


def capture_from_button(page, button, dest: Path, expect_ext: str,
                        timeout: int = 60000) -> bool:
    """Click a known download button and save the result if it validates."""
    try:
        with page.expect_download(timeout=timeout) as dl_info:
            button.click()
        return _save_validated(dl_info.value, dest, expect_ext)
    except PWTimeout:
        log("  ERROR: clicked download but no file started in time.")
        return False


def json_toolbar_candidates(page):
    """Return the unlabeled icon buttons of the JSON-config viewer toolbar
    (top-right of the JSON panel, ~y=205). One of them is the download arrow;
    the others are copy / wrap. We try each and keep the one that yields a
    .json download. Labeled buttons (dark-mode, settings, the assertions
    'Download') are excluded so they can never collide here."""
    out = []
    btns = page.locator("button:has(span.fui-Button__icon)")
    for i in range(btns.count()):
        b = btns.nth(i)
        try:
            if not b.is_visible():
                continue
            label = (b.get_attribute("aria-label") or b.get_attribute("title") or "").strip()
            if label:
                continue
            box = b.bounding_box()
            if not box:
                continue
            # JSON viewer toolbar sits in the upper-right of the content area
            if box["x"] > 1400 and 150 < box["y"] < 280:
                out.append((box["x"], b))
        except Exception:
            continue
    out.sort(key=lambda t: t[0])
    return [b for _, b in out]


def capture_json_config(page, dest: Path) -> bool:
    """Try each JSON-viewer toolbar icon until one produces a .json download."""
    candidates = json_toolbar_candidates(page)
    if not candidates:
        log("  ERROR: no JSON-config toolbar icons found.")
        return False
    for idx, btn in enumerate(candidates):
        try:
            with page.expect_download(timeout=8000) as dl_info:
                btn.click()
        except PWTimeout:
            continue  # copy / wrap button -> no download; try the next
        if _save_validated(dl_info.value, dest, ".json"):
            return True
    log("  ERROR: none of the JSON-config toolbar icons produced a .json file.")
    return False


def fetch_run(page, run_id: str, out_dir: Path, login_timeout: int) -> bool:
    url = f"https://seval.microsoft.com/detail/{run_id}"
    log(f"run {run_id}: navigating to {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if not wait_for_detail(page, login_timeout):
        log(f"  ERROR: detail page for {run_id} never rendered (SSO?).")
        return False

    ok = True

    # --- Assertions CSV (LM Checklist -> Assertion View -> Download) -------
    log("  opening 'LM Checklist' tab...")
    if not open_tab(page, re.compile(r"LM\s*Checklist", re.I)):
        log("  ERROR: 'LM Checklist' tab not found.")
        ok = False
    else:
        if not set_assertion_view(page):
            log("  WARN: could not confirm 'Assertion View'; continuing.")
        page.wait_for_timeout(1500)
        # The assertions grid exposes a button with aria-label exactly 'Download'.
        dl_btn = page.get_by_role("button", name=re.compile(r"^Download$", re.I))
        if dl_btn.count() == 0:
            log("  ERROR: assertions 'Download' button not found.")
            ok = False
        else:
            ok = capture_from_button(
                page, dl_btn.first, out_dir / f"{run_id}_assertions.csv", ".csv") and ok

    # --- Settings JSON (JSON config tab -> viewer toolbar download) -------
    log("  opening 'JSON config' tab...")
    if not open_tab(page, re.compile(r"JSON\s*config", re.I)):
        log("  ERROR: 'JSON config' tab not found.")
        ok = False
    else:
        page.wait_for_timeout(1500)
        ok = capture_json_config(page, out_dir / f"{run_id}_settings.json") and ok

    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, type=Path,
                    help="Descriptor JSON listing the two SEVAL runs (sevals[]).")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Destination folder. Default: "
                         "seval-input/regression_<control-id>_<experiment-id>.")
    ap.add_argument("--login-timeout", type=int, default=300,
                    help="Seconds to wait for SSO / page render (default 300).")
    args = ap.parse_args()

    if not args.input.exists():
        sys.exit(f"[seval-fetch] input not found: {args.input}")
    descriptor = json.loads(args.input.read_text(encoding="utf-8"))
    sevals = descriptor.get("sevals") or []
    if len(sevals) != 2:
        sys.exit(f"[seval-fetch] expected exactly 2 entries in sevals[]; got {len(sevals)}.")

    runs = []
    for i, s in enumerate(sevals):
        rid = run_id_from_url(s["url"])
        runs.append({"id": rid, "role": "control" if i == 0 else "experiment", **s})
    control, experiment = runs[0], runs[1]

    out_dir = args.out_dir or (REPO_ROOT / "seval-input" /
                               f"regression_{control['id']}_{experiment['id']}")
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"control    run = {control['id']}  ({control.get('date','?')}, "
        f"control={control.get('control')} / experiment={control.get('experiment')})")
    log(f"experiment run = {experiment['id']}  ({experiment.get('date','?')})")
    log(f"output dir     = {out_dir}")

    all_ok = True
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="msedge",
            headless=False,
            accept_downloads=True,
            viewport={"width": 1600, "height": 1000},
            args=["--start-maximized"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for r in (control, experiment):
            all_ok = fetch_run(page, r["id"], out_dir, args.login_timeout) and all_ok
        ctx.close()

    log("---- result ----")
    for f in sorted(out_dir.glob("*")):
        log(f"  {f.name}  ({f.stat().st_size:,} bytes)")

    if all_ok:
        c_date = to_iso_date(control.get("date", ""))
        e_date = to_iso_date(experiment.get("date", ""))
        log("ALL DOWNLOADS OK. Ready for seval-regression-analyze:")
        log(f"  --control-csv  {out_dir / (control['id'] + '_assertions.csv')}")
        log(f"  --control-json {out_dir / (control['id'] + '_settings.json')}")
        log(f"  --control-id {control['id']} --control-name {control.get('control','Mainline')} --control-date {c_date}")
        log(f"  --experiment-csv  {out_dir / (experiment['id'] + '_assertions.csv')}")
        log(f"  --experiment-json {out_dir / (experiment['id'] + '_settings.json')}")
        log(f"  --experiment-id {experiment['id']} --experiment-name {experiment.get('experiment','CodeGen')} --experiment-date {e_date}")
        return 0
    log("ONE OR MORE DOWNLOADS FAILED — see errors above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
