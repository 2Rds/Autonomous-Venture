#!/usr/bin/env python3
"""Standalone Cloudflare Browser Run client for Claude Code (Sean's machine).

DELIBERATELY SEPARATE from AgentCorp's shared/browser/* (owner decision
2026-08-09): this file shares no code with the production agents, uses its own
Cloudflare API token (CC_BROWSER_API_TOKEN — a token scoped separately from the
agents' CF_BROWSER_API_TOKEN so Claude Code usage is attributable apart from
business COGS), and self-meters every session to ~/.claude/tools/
browser_usage.jsonl so the operator can back this usage out of the Cloudflare
invoice (Browser Rendering bills per ACCOUNT; the token split gives audit
attribution, the JSONL gives the dollar figure).

One-shot lifecycle per invocation: connect -> act -> close, so a crashed run
bills at most KEEP_ALIVE_MS. A failed close keeps Cloudflare billing until
keep_alive expires — that is why keep_alive here is 120s, not the agents'
run-ceiling-sized bound.

Usage:
  browser_run.py URL [--text] [--max-chars N] [--screenshot PATH] [--full-page]
                     [--click TARGET]... [--type TARGET=TEXT]... [--wait-ms N]
Actions run in order: goto, then clicks/types in the order given on the
command line, then text digest (default on) and/or screenshot.

Credentials: ~/.claude/tools/browser_run.env (chmod 600, never committed):
  CC_BROWSER_ACCOUNT_ID=...
  CC_BROWSER_API_TOKEN=...

Hard rules ported from the production tools (they are right for this context
too): http/https only, checked before navigation, on every document hop via a
fail-closed route guard, and re-checked after landing; never type into a
password-typed field; never type secret-shaped text (sk-/Bearer/AKIA/gh*/JWT).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ENV_FILE = TOOLS_DIR / "browser_run.env"
USAGE_LOG = TOOLS_DIR / "browser_usage.jsonl"

# Cloudflare's keep_alive is an INACTIVITY bound, not a session cap — a long
# invocation refreshes it with every action, and this script closes in
# `finally:` regardless. So this number only bounds what a CRASHED run can
# bill: 5 min of margin for slow pages, still a tight disaster bound (the
# agents size theirs to a whole turn because they idle between tool calls;
# this one-shot design never idles). If a flow ever needs one live page
# across multiple invocations, that is a --session daemon mode, not a bigger
# keep_alive.
KEEP_ALIVE_MS = 300_000
NAV_TIMEOUT_MS = 30_000
ACT_TIMEOUT_MS = 15_000
SHOT_TIMEOUT_MS = 20_000
USD_PER_HOUR = 0.09  # Cloudflare Browser Run metered rate (verified 2026-08-07)

# Same five token shapes the agents refuse (shared/flight_recorder._SECRET_RE).
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{12,}|Bearer\s+[A-Za-z0-9._\-]{12,}|AKIA[0-9A-Z]{16}|"
    r"gh[pousr]_[A-Za-z0-9]{20,}|"
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{6,})"
)

_PRIVATE_HOST_RE = re.compile(
    r"^(localhost|127\.|10\.|192\.168\.|169\.254\.|0\.0\.0\.0|\[?::1\]?|"
    r"172\.(1[6-9]|2\d|3[01])\.)",
    re.IGNORECASE,
)

_SELECTOR_RE = re.compile(r"^[#.\[]|^[a-zA-Z][\w-]*([#.\[:][^ ]*)?$")


def _navigable(url: str) -> tuple[bool, str]:
    """http/https only, no localhost/private-literal hosts. Total, never raises.
    (No DNS resolution on purpose — standalone; the residual matches the
    production policy's accepted caveat, and the browser egresses from
    Cloudflare's network, not this machine.)"""
    from urllib.parse import urlparse

    u = (url or "").strip()
    if not u.lower().startswith(("http://", "https://")):
        return False, "only http/https URLs are allowed"
    try:
        host = urlparse(u).hostname or ""
    except ValueError:
        return False, "malformed URL"
    if not host or _PRIVATE_HOST_RE.match(host):
        return False, "localhost/private hosts are blocked"
    return True, ""


def _load_env() -> tuple[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE} — needs CC_BROWSER_ACCOUNT_ID and CC_BROWSER_API_TOKEN")
    vals: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()
    account = vals.get("CC_BROWSER_ACCOUNT_ID", "")
    token = vals.get("CC_BROWSER_API_TOKEN", "")
    if not account or not token or token == "PASTE_TOKEN_HERE":
        sys.exit(f"{ENV_FILE} is incomplete — set CC_BROWSER_ACCOUNT_ID and CC_BROWSER_API_TOKEN")
    return account, token


def _digest(title: str, text: str, max_chars: int) -> str:
    t = re.sub(r"[ \t\r\f\v]+", " ", f"{title}\n\n{text}")
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if len(t) > max_chars:
        t = t[:max_chars] + f"\n… [truncated at {max_chars} chars]"
    return t


def _log_usage(seconds: float, url_host: str, outcome: str) -> None:
    """Self-metering — the COGS carve-out. Fail-soft: a logging error must not
    fail the run, but say so (a silent metering gap is untracked spend)."""
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seconds": round(seconds, 1),
        "est_usd": round(seconds / 3600 * USD_PER_HOUR, 6),
        "host": url_host,
        "outcome": outcome,
    }
    try:
        with USAGE_LOG.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except OSError as exc:
        print(f"[browser_run] WARNING: usage log write failed ({exc}) — "
              f"{seconds:.1f}s of browser time is untracked", file=sys.stderr)


async def _run(args) -> int:
    from urllib.parse import urlparse

    from playwright.async_api import async_playwright

    ok, why = _navigable(args.url)
    if not ok:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    # Secret-shaped text refuses BEFORE the (billed) connect — the in-loop
    # password-field check still runs per-field after navigation.
    for kind, value in args.steps:
        if kind == "type" and _SECRET_RE.search(value.partition("=")[2]):
            print("refused: that text looks like a credential/token — typing "
                  "secrets into websites is not allowed", file=sys.stderr)
            return 2

    account, token = _load_env()
    endpoint = (f"wss://api.cloudflare.com/client/v4/accounts/{account}"
                f"/browser-rendering/devtools/browser?keep_alive={KEEP_ALIVE_MS}")

    started = time.monotonic()
    outcome = "error"
    pw = browser = None
    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.connect_over_cdp(
            endpoint, headers={"Authorization": f"Bearer {token}"}, timeout=20_000,
        )
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        # Cloudflare's default viewport is phone-narrow — responsive navs
        # collapse behind hamburgers and visible-text clicks time out on
        # elements that exist but aren't shown. Desktop-by-default.
        w, _, h = (args.viewport or "1280x800").partition("x")
        await page.set_viewport_size({"width": int(w or 1280), "height": int(h or 800)})

        async def _route_guard(route):
            # Fail CLOSED: an exception in a route handler leaves the request
            # neither aborted nor continued (a hang) — abort instead.
            try:
                if route.request.resource_type == "document":
                    g_ok, _ = _navigable(route.request.url)
                    if not g_ok:
                        await route.abort()
                        return
                await route.continue_()
            except Exception:
                try:
                    await route.abort()
                except Exception:
                    pass

        await page.route("**/*", _route_guard)
        await page.goto(args.url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        if args.wait_ms:
            await page.wait_for_timeout(min(args.wait_ms, 15_000))

        landed = page.url
        ok, why = _navigable(landed)
        if not ok:
            print(f"refused: page redirected to a blocked address ({why}); "
                  f"content not read", file=sys.stderr)
            outcome = "blocked_redirect"
            return 2

        # Interactions, in command-line order.
        for kind, value in args.steps:
            if kind == "click":
                loc = (page.get_by_role("button", name=value)
                       .or_(page.get_by_role("link", name=value))
                       .or_(page.get_by_text(value, exact=False)))
                if _SELECTOR_RE.match(value):
                    loc = loc.or_(page.locator(value))
                await loc.first.click(timeout=ACT_TIMEOUT_MS)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5_000)
                except Exception:
                    pass  # no navigation happened — fine
            else:  # type
                target, _, text = value.partition("=")
                if _SECRET_RE.search(text):
                    print("refused: that text looks like a credential/token — "
                          "typing secrets into websites is not allowed", file=sys.stderr)
                    outcome = "refused_secret"
                    return 2
                loc = page.get_by_label(target).or_(page.get_by_placeholder(target))
                if _SELECTOR_RE.match(target):
                    loc = loc.or_(page.locator(target))
                field = loc.first
                if ((await field.get_attribute("type")) or "").lower() == "password":
                    print("refused: password fields are off-limits", file=sys.stderr)
                    outcome = "refused_password"
                    return 2
                await field.fill(text, timeout=ACT_TIMEOUT_MS)
            g_ok, why = _navigable(page.url)
            if not g_ok and page.url not in ("", "about:blank"):
                print(f"refused: navigation landed on a blocked address ({why})",
                      file=sys.stderr)
                outcome = "blocked_redirect"
                return 2

        if args.screenshot:
            png = await page.screenshot(full_page=args.full_page,
                                        timeout=SHOT_TIMEOUT_MS, type="png")
            Path(args.screenshot).write_bytes(png)
            print(f"screenshot: {args.screenshot} ({len(png)} bytes)")

        if args.text or not args.screenshot:
            body = await page.evaluate("() => document.body ? document.body.innerText : ''")
            title = await page.title()
            print(f"URL: {page.url}\n")
            print(_digest(title, body, args.max_chars))

        outcome = "ok"
        return 0
    except Exception as exc:
        # Clean one-liner, not a Playwright stack dump (which embeds selector
        # traces). The raw class name is enough to act on; exit 1 = failed
        # action, exit 2 = policy refusal.
        msg = str(exc).split("\n")[0][:200]
        print(f"error: {type(exc).__name__}: {msg}", file=sys.stderr)
        return 1
    finally:
        for closer in (browser, pw):
            try:
                if closer is pw and pw is not None:
                    await pw.stop()
                elif closer is not None:
                    await closer.close()
            except Exception as exc:
                # A failed close keeps Cloudflare billing until keep_alive
                # expires — always say so.
                print(f"[browser_run] WARNING: close failed ({exc}); Cloudflare "
                      f"keep_alive ({KEEP_ALIVE_MS/1000:.0f}s) bounds the spend",
                      file=sys.stderr)
        _log_usage(time.monotonic() - started,
                   urlparse(args.url).hostname or "", outcome)


class _StepAction(argparse.Action):
    """Preserve --click/--type interleaving order."""

    def __call__(self, parser, ns, value, option_string=None):
        steps = getattr(ns, "steps", None) or []
        steps.append(("click" if option_string == "--click" else "type", value))
        ns.steps = steps


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("url")
    ap.add_argument("--text", action="store_true", help="print page text digest (default if no --screenshot)")
    ap.add_argument("--max-chars", type=int, default=4000)
    ap.add_argument("--screenshot", metavar="PATH")
    ap.add_argument("--full-page", action="store_true")
    ap.add_argument("--click", action=_StepAction, metavar="TARGET",
                    help="click by visible text / role name / CSS selector (repeatable)")
    ap.add_argument("--type", action=_StepAction, metavar="TARGET=TEXT",
                    help="fill a field by label/placeholder/CSS (repeatable; no secrets)")
    ap.add_argument("--wait-ms", type=int, default=0, help="extra settle time after load")
    ap.add_argument("--viewport", metavar="WxH", default="1280x800")
    args = ap.parse_args()
    if not hasattr(args, "steps"):
        args.steps = []

    import asyncio

    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
