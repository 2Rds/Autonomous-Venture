"""CreatorStacked agent — Telegram interface for the automated content/affiliate venture.

Chat is read-only reporting: the Claude call has no tool access at all — it reasons only over
the conversation text, same guardrail pattern the rest of the fleet uses (see daily-brief).
Real capability is a small set of operator-only commands, each a hand-written function, not
something the chat model can invoke itself:

  status            — repo state + open Link spend-requests
  spend             — create a Link spend-request (money moves only after Sean approves it
                       himself in the Link app — see _create_spend_request)
  draft email       — a separate, WebFetch-enabled Claude call drafts an email; produces a
                       draft only, sends nothing (see _draft_options)
  draft application — same, but for an affiliate-program application. THERE IS NO SEND PATH
                       FOR THIS ONE, by design: submitting an account/application on a
                       third-party platform is never something this process does, approved
                       or not — see _handle_send. Sean copies the draft into the form himself.
  send <id>         — sends an `email`-kind draft via AgentMail, after Sean has seen it here
                       first. Refuses `application`-kind drafts unconditionally.
  drafts            — list open (unsent) drafts
  pause / resume    — kill switch; gates spend/draft/send/chat, never the commands themselves

Scope is fixed: content/affiliate for course-creator/coaching tools only. See ../PLAN.md.
Widening scope further (a new business domain, direct payment processing, autonomous
LLM-initiated spend or send instead of an operator-issued command) is a deliberate later step
on the trust ladder, not something this bot does on its own — see README "Trust ladder".
"""

import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, TextBlock

# Under systemd, EnvironmentFile= injects these vars and InaccessiblePaths= hides the file
# from this process — unreadable .env is expected there, same as the rest of the fleet.
try:
    load_dotenv(Path(__file__).parent / ".env")
except PermissionError:
    pass

log = logging.getLogger("creatorstacked-bot")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
# httpx logs the full request URL at INFO, and the Telegram Bot API embeds the bot token
# directly in the URL path (api.telegram.org/bot<TOKEN>/...) -- left at INFO, every request
# writes the token to the journal in plaintext. This is the only line standing between that
# and every log line the app itself emits, so it stays above LOG_LEVEL regardless of setting.
logging.getLogger("httpx").setLevel(logging.WARNING)

DATA_DIR = Path(__file__).parent / ".data"
STATE_FILE = DATA_DIR / "state.json"
DRAFTS_DIR = DATA_DIR / "drafts"
REPO_ROOT = Path(__file__).resolve().parents[1]  # .../Autonomous-Venture

# Optional — drafting works without these, `send` doesn't. Missing means "feature off",
# same fail-soft-per-source pattern as the rest of the fleet (e.g. daily-brief), not a
# startup crash for a capability Sean hasn't finished wiring up yet.
AGENTMAIL_API_KEY = os.environ.get("AGENTMAIL_API_KEY", "").strip()
AGENTMAIL_INBOX_ID = os.environ.get("AGENTMAIL_INBOX_ID", "").strip()

SCOPE_PROMPT = """You are the operating agent for CreatorStacked (creatorstacked.com), an
automated content + recurring-affiliate site for online course creators and coaches (Kajabi,
Teachable, Podia, Thinkific and similar tools). You're talking to Sean over Telegram.

Fixed scope, not yours to widen: content/affiliate for course-creator and coaching tools only.
No cold-email/outbound tooling (that's AgentCorp's own market, and none of its IP is used here).
No first-party paid products (that needs a Stripe account, which is Sean's to create if that day
ever comes, not yours).

You have no tool access in this chat — you can discuss the plan and reason about what to do next,
but you cannot read files, run commands, or spend money from here. If checking status or
requesting a purchase is what's needed, tell Sean to use the `status` or `spend` command — those
are separate operator commands this process handles directly, not things you invoke yourself
mid-conversation. If you're not sure whether something is in scope, say so and ask rather than
guessing."""

HELP_TEXT = (
    "Commands:\n"
    "`status` — repo state + open spend-requests\n"
    "`spend <dollars>|<merchant>|<merchant url>|<reason>` — request a purchase "
    "(needs your approval in the Link app before anything is charged)\n"
    "`draft email <to>|<subject>|<brief>` — draft an email (sends nothing)\n"
    "`draft application <program>|<program url>` — draft affiliate-program application "
    "answers for you to submit yourself (there is no send for this one, ever)\n"
    "`send <id>` — send an email draft via AgentMail (only works on `email` drafts)\n"
    "`drafts` — list open drafts\n"
    "`pause` / `resume` — kill switch\n"
    "Anything else is a normal chat message to the agent (read-only — it can't act on it)."
)


def _require_env(name: str, hint: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set — {hint}")
    return value


TELEGRAM_BOT_TOKEN = ""
OPERATOR_TELEGRAM_ID = 0


def _init_config() -> None:
    global TELEGRAM_BOT_TOKEN, OPERATOR_TELEGRAM_ID
    TELEGRAM_BOT_TOKEN = _require_env("TELEGRAM_BOT_TOKEN", "bot token from @BotFather")
    OPERATOR_TELEGRAM_ID = int(_require_env(
        "OPERATOR_TELEGRAM_ID",
        "Sean's numeric Telegram user ID — message @userinfobot to get it",
    ))


# ---------------------------------------------------------------- state

_state: dict = {"paused": False}


def _load_state() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if STATE_FILE.exists():
        try:
            raw = json.loads(STATE_FILE.read_text())
            if isinstance(raw, dict):
                _state["paused"] = bool(raw.get("paused"))
        except (json.JSONDecodeError, OSError):
            log.warning("%s unreadable — starting fresh (paused resets to False)", STATE_FILE)


def _save_state() -> bool:
    try:
        STATE_FILE.write_text(json.dumps(_state))
        return True
    except OSError:
        log.error("could not persist state to %s", STATE_FILE)
        return False


# ---------------------------------------------------------------- claude (read-only chat)

def _claude_options() -> ClaudeAgentOptions:
    # No tool access at all — this call reasons only over the conversation text. Real
    # capability lives in the hand-written command handlers below, never here. Same
    # three-layer guardrail as the rest of the fleet: disallowed_tools is the enumerated
    # restriction (not proven exhaustive against every CLI build), setting_sources=[] keeps
    # settings files and CLAUDE.md out of the session, strict_mcp_config=True is what
    # actually stops MCP servers from loading.
    return ClaudeAgentOptions(
        system_prompt=SCOPE_PROMPT,
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        disallowed_tools=["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
                          "Read", "Grep", "Glob", "WebFetch", "WebSearch",
                          "Task", "Agent", "TodoWrite", "SlashCommand", "Skill",
                          "BashOutput", "KillShell", "ExitPlanMode",
                          "EnterPlanMode", "AskUserQuestion", "Artifact",
                          "ToolSearch", "Monitor", "EnterWorktree",
                          "ExitWorktree"],
        max_turns=int(os.environ.get("MAX_TURNS", "8")),
        model=os.environ.get("AGENT_MODEL", "claude-sonnet-5"),
        effort=os.environ.get("AGENT_EFFORT", "medium"),
    )


async def _ask_claude(prompt: str) -> str:
    parts: list[str] = []
    async with ClaudeSDKClient(options=_claude_options()) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
    return "\n".join(parts) if parts else "(no response)"


# ---------------------------------------------------------------- claude (drafting)

def _draft_options() -> ClaudeAgentOptions:
    # Same three-layer guardrail as _claude_options(), except WebFetch is deliberately left
    # OUT of the disallow list — the one tool this call gets, so a draft can be grounded in a
    # real page instead of a guess. Everything that could actually submit/send/write anything
    # is still disallowed; drafting produces text, nothing else, regardless of what it looked up.
    return ClaudeAgentOptions(
        system_prompt=SCOPE_PROMPT + "\n\nYou are drafting text only — an email or an "
                      "affiliate-program application — never sending or submitting anything "
                      "yourself; a human does that after reading your draft. Use WebFetch to "
                      "check any factual claim about a program or recipient before writing it "
                      "down; never state something as fact you have not looked up this session.",
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        disallowed_tools=["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
                          "Read", "Grep", "Glob", "WebSearch",
                          "Task", "Agent", "TodoWrite", "SlashCommand", "Skill",
                          "BashOutput", "KillShell", "ExitPlanMode",
                          "EnterPlanMode", "AskUserQuestion", "Artifact",
                          "ToolSearch", "Monitor", "EnterWorktree",
                          "ExitWorktree"],  # WebFetch intentionally absent from this list
        max_turns=int(os.environ.get("MAX_TURNS", "8")),
        model=os.environ.get("AGENT_MODEL", "claude-sonnet-5"),
        effort=os.environ.get("AGENT_EFFORT", "medium"),
    )


async def _run_draft(prompt: str) -> str:
    parts: list[str] = []
    async with ClaudeSDKClient(options=_draft_options()) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
    return "\n".join(parts) if parts else "(no draft produced)"


# ---------------------------------------------------------------- draft storage

def _save_draft(kind: str, meta: dict, body: str) -> str:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    draft_id = uuid.uuid4().hex[:8]
    record = {"id": draft_id, "kind": kind, "status": "draft", "body": body, **meta}
    (DRAFTS_DIR / f"{draft_id}.json").write_text(json.dumps(record, indent=2))
    return draft_id


def _load_draft(draft_id: str) -> dict | None:
    path = DRAFTS_DIR / f"{draft_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _mark_draft_sent(draft: dict) -> None:
    draft["status"] = "sent"
    (DRAFTS_DIR / f"{draft['id']}.json").write_text(json.dumps(draft, indent=2))


def _list_open_drafts() -> list[dict]:
    DRAFTS_DIR.mkdir(parents=True, exist_ok=True)
    drafts = []
    for f in sorted(DRAFTS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("status") == "draft":
            drafts.append(d)
    return drafts


def _handle_drafts_list() -> str:
    drafts = _list_open_drafts()
    if not drafts:
        return "No open drafts."
    return "\n".join(
        f"`{d['id']}` [{d['kind']}] " + (d.get("to") or d.get("program", ""))
        for d in drafts
    )


# ---------------------------------------------------------------- draft / send commands

async def _handle_draft_email(arg: str) -> str:
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) != 3:
        return ("Format: `draft email <to>|<subject>|<brief>`\n"
                 "e.g. `draft email partners@kajabi.com|Affiliate partnership inquiry|"
                 "ask about joining their affiliate program for a course-creator tools "
                 "review site`")
    to, subject, brief = parts
    prompt = (f"Draft an email.\nTo: {to}\nSubject: {subject}\nBrief: {brief}\n\n"
              f"Write only the email body (plain text, no subject line repeated in the "
              f"body). Sign off as the CreatorStacked team, creatorstacked@agentmail.to.")
    body = await _run_draft(prompt)
    draft_id = _save_draft("email", {"to": to, "subject": subject}, body)
    return (f"Draft `{draft_id}` (email to {to}):\n\n{body}\n\n"
            f"`send {draft_id}` to send as-is, or just tell me what to change.")


async def _handle_draft_application(arg: str) -> str:
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) != 2:
        return ("Format: `draft application <program name>|<program url>`\n"
                 "e.g. `draft application Kajabi|https://kajabi.com/affiliates`")
    program, url = parts
    prompt = (f"Look up {url} and draft answers for {program}'s affiliate program "
              f"application, as CreatorStacked (creatorstacked.com, "
              f"creatorstacked@agentmail.to) — a content site reviewing tools for online "
              f"course creators and coaches. Only state facts about {program} you actually "
              f"found at that URL; say plainly if the page didn't have what you needed.")
    body = await _run_draft(prompt)
    draft_id = _save_draft("application", {"program": program, "url": url}, body)
    return (f"Draft `{draft_id}` (application to {program}) — for you to submit yourself, "
            f"there is no `send` for this one:\n\n{body}")


async def _handle_send(draft_id: str) -> str:
    draft_id = draft_id.strip()
    draft = _load_draft(draft_id)
    if draft is None or draft.get("status") != "draft":
        return f"No open draft `{draft_id}`."
    if draft["kind"] != "email":
        return ("Applications don't get an automated send — submitting an account or "
                 "application on a third-party platform is never something this agent "
                 "does, approved or not. Copy the draft into the form yourself.")
    if not AGENTMAIL_API_KEY or not AGENTMAIL_INBOX_ID:
        return "AGENTMAIL_API_KEY / AGENTMAIL_INBOX_ID not configured yet — can't send."
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://api.agentmail.to/v0/inboxes/{AGENTMAIL_INBOX_ID}/messages/send",
            headers={"Authorization": f"Bearer {AGENTMAIL_API_KEY}"},
            json={"to": draft["to"], "subject": draft["subject"], "text": draft["body"]},
        )
    if resp.status_code != 200:
        return f"Send failed ({resp.status_code}): {resp.text[:300]}"
    _mark_draft_sent(draft)
    return f"Sent `{draft_id}` to {draft['to']}."


# ---------------------------------------------------------------- link-cli (spend-requests)

def _create_spend_request(amount_cents: int, merchant_name: str, merchant_url: str, reason: str) -> str:
    """The only thing in this process that touches money, and even this only *requests* it —
    nothing is charged until Sean approves in the Link app on his own device. Deliberately not
    exposed to the Claude call above as a tool; only reachable via the `spend` command, so a
    purchase always traces back to an explicit operator-issued command, never a chat reply."""
    context = f"CreatorStacked agent spend request (via Telegram command) — {reason}"
    if len(context) < 100:
        context += ". " * ((100 - len(context)) // 2 + 1)  # link-cli requires >=100 chars
    result = subprocess.run(
        [
            "link-cli", "spend-request", "create",
            "--credential-type", "card",
            "--amount", str(amount_cents),
            "--currency", "usd",
            "--merchant-name", merchant_name,
            "--merchant-url", merchant_url,
            "--context", context,
            "--total", f"type:total,display_text:Total,amount:{amount_cents}",
        ],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return f"spend-request creation failed:\n{result.stderr.strip()}"
    return result.stdout.strip()


def _handle_spend(arg: str) -> str:
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) != 4:
        return ("Format: `spend <dollars>|<merchant>|<merchant url>|<reason>`\n"
                 "e.g. `spend 15|Porkbun|https://porkbun.com|renew creatorstacked.com`")
    amount_str, merchant_name, merchant_url, reason = parts
    try:
        amount_cents = round(float(amount_str) * 100)
    except ValueError:
        return f"Couldn't parse `{amount_str}` as a dollar amount."
    if amount_cents <= 0:
        return "Amount must be positive."
    return _create_spend_request(amount_cents, merchant_name, merchant_url, reason)


def _handle_status() -> str:
    git_rev = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip() or "no commits"
    spend_requests = subprocess.run(
        ["link-cli", "spend-request", "list"], capture_output=True, text=True,
    ).stdout.strip() or "(none)"
    return (
        f"paused: {_state['paused']}\n"
        f"repo: {git_rev}\n"
        f"open spend-requests:\n{spend_requests}"
    )


# ---------------------------------------------------------------- operator commands

async def _route(text: str) -> str:
    lowered = text.strip().lower()

    # Always available, even while paused — that's what makes it a kill switch and not
    # just a mood.
    if lowered == "status":
        return _handle_status()
    if lowered == "drafts":
        return _handle_drafts_list()
    if lowered == "pause":
        _state["paused"] = True
        ok = _save_state()
        return "Paused." + ("" if ok else "\n⚠️ could not persist — a restart would resume me.")
    if lowered == "resume":
        _state["paused"] = False
        ok = _save_state()
        return "Resumed." + ("" if ok else "\n⚠️ could not persist — a restart would re-pause me.")
    if lowered in ("help", "commands"):
        return HELP_TEXT

    if _state["paused"]:
        return ("Paused — `resume` to re-enable spend/draft/send and chat. "
                 "`status`/`drafts`/`resume`/`help` still work.")

    if lowered.startswith("spend "):
        return _handle_spend(text[len("spend "):])
    if lowered.startswith("draft email "):
        return await _handle_draft_email(text[len("draft email "):])
    if lowered.startswith("draft application "):
        return await _handle_draft_application(text[len("draft application "):])
    if lowered.startswith("send "):
        return await _handle_send(text[len("send "):])

    return await _ask_claude(text)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text or not update.effective_chat:
        return
    user_id = update.effective_user.id if update.effective_user else None
    chat_id = update.effective_chat.id

    if user_id != OPERATOR_TELEGRAM_ID:
        await context.bot.send_message(chat_id=chat_id, text="I only take commands from my operator.")
        return

    reply = await _route(update.message.text)
    await context.bot.send_message(chat_id=chat_id, text=reply)


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    return app


def main() -> None:
    _init_config()
    _load_state()
    app = build_app()
    log.info("creatorstacked-bot starting (long-polling, no public endpoint)")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
