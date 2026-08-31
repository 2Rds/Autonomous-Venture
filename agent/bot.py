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
  pipeline          — run one content-pipeline cycle now (also runs on a schedule, see
                       content_pipeline_loop). Researches one new article, writes it to
                       site/content/articles/ as status: draft, commits and pushes it, and
                       DMs Sean. It NEVER sets status: published — see _write_article_file.
  pause / resume    — kill switch; gates spend/draft/send/pipeline/chat, never the commands
                       themselves

This is the durable-autonomy piece: content_pipeline_loop runs inside this same systemd
service (Restart=always, its own droplet), not inside any interactive Claude Code session —
it keeps running whether or not anyone is watching, which is the actual point of it.

Scope is fixed: content/affiliate for course-creator/coaching tools only. See ../PLAN.md.
Widening scope further (a new business domain, direct payment processing, autonomous
LLM-initiated spend or send instead of an operator-issued command, or the pipeline ever
setting status: published itself) is a deliberate later step on the trust ladder, not
something this bot does on its own — see README "Trust ladder".
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import subprocess
import uuid
from datetime import date, datetime, timezone
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
ARTICLES_DIR = REPO_ROOT / "site" / "content" / "articles"

# Optional — drafting works without these, `send` doesn't. Missing means "feature off",
# same fail-soft-per-source pattern as the rest of the fleet (e.g. daily-brief), not a
# startup crash for a capability Sean hasn't finished wiring up yet.
AGENTMAIL_API_KEY = os.environ.get("AGENTMAIL_API_KEY", "").strip()
AGENTMAIL_INBOX_ID = os.environ.get("AGENTMAIL_INBOX_ID", "").strip()

# Optional — the pipeline still researches and writes the article locally without this, it
# just can't push, and says so. A fine-grained PAT scoped to just this repo (Contents: write),
# not a full-access token.
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
CONTENT_PIPELINE_INTERVAL_HOURS = float(os.environ.get("CONTENT_PIPELINE_INTERVAL_HOURS", "168"))

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
    "`pipeline` — run one content-pipeline cycle now (researches + drafts + commits one new "
    "article, always as status: draft; also runs on its own schedule)\n"
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
                if raw.get("last_pipeline_run"):
                    _state["last_pipeline_run"] = raw["last_pipeline_run"]
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


# ---------------------------------------------------------------- content pipeline

# Pasted verbatim from the human-writing skill's system-prompt-block.md, per that skill's own
# instruction ("keep the rules concrete and verbatim"). This call can't invoke the Skill tool
# itself (Skill is disallowed below, like everything else that isn't WebFetch/WebSearch), so
# the rules have to be embedded directly rather than fetched at call time.
_WRITING_STYLE_BLOCK = """## Writing style (non-negotiable)

Everything you write goes to a real person who deletes anything that reads like AI. Follow these rules in every message, email, and document:

**Never use:**
- Em dashes (—). This is absolute: not in prose, not in headings, section labels, or titles ("Section one: the problem", never "Section one — the problem"), and not even if the user's own message uses them. Use a comma, period, colon, or parentheses instead.
- These words: delve, leverage, utilize, seamless, robust, streamline, elevate, supercharge, unlock, unleash, empower, harness, foster, holistic, synergy, game-changer, cutting-edge, revolutionize, transformative, realm, journey, "navigate", "in today's fast-paced world", "ever-evolving", "dive in". Exception: when one of these is the precise domain term ("leverage ratio" in finance, "robust" in engineering), use it; the ban is on hype usage, not technical meaning.
- These patterns: negate-then-pivot contrast in ANY wording, not just the phrase "not just" ("It's not just X, it's Y", "That's not a discipline problem, that's a staffing problem", "You're not buying software. You're bringing on a team"). If a sentence negates something only to pivot to your real claim, delete the negation and state the claim. Also banned: "not only X but also Y"; rule-of-three lists ("faster, smarter, better"); "whether you're a X, Y, or Z"; rhetorical-question hooks ("Struggling with X?"); false ranges ("from startups to enterprises").
- These closers: "In conclusion", "Ultimately", "At the end of the day", or any final sentence that restates what you already said. End on the last substantive point.
- Bold/italic emphasis mid-sentence for emphasis (bullet lists of parallel data, like pricing tiers, are fine).

**Always:**
- Be specific: real numbers, names, and dates instead of abstractions.
- Vary sentence length; fragments are fine.
- Use contractions (it's, don't, you're).
- Commit to one clear claim instead of hedging ("it's worth noting", "arguably" are banned).
- Before finishing, scan your own draft for em dashes, banned words, negate-then-pivot sentences, and triadic lists; rewrite any hit."""

_PIPELINE_SYSTEM_PROMPT = f"""{SCOPE_PROMPT}

You are extending CreatorStacked's content library, unsupervised — this runs on a schedule with
no one reviewing your topic choice before you research and write. What IS still reviewed by Sean
before anything goes live is the draft itself (see below), so the discipline here matters more,
not less, than in a supervised call.

Personas already established for this site: fitness coaches, music teachers, consultants,
cohort-based course creators. Programs in scope: Kajabi, Teachable, Podia, Thinkific, and
similar tools for online course creators and coaches.

Pick ONE new, specific, long-tail angle not already covered by the existing article slugs you
are given. Avoid generic "best tool" listicles — narrow to a persona or a specific decision
(a pricing tier, a feature gap, a specific comparison) the way the existing articles do.

Research it with WebSearch, then verify every factual claim (pricing, fees, feature limits)
against the vendor's own page with WebFetch before writing it down. Never state a number or
feature claim you have not personally verified this session — an aggregator or review site is
not a source, only the vendor's own page is. If you cannot verify a specific number, write
around it rather than guess.

{_WRITING_STYLE_BLOCK}

Output EXACTLY in this format and nothing else, no preamble or closing remarks outside it:

TITLE: <title, no colon-subtitle construction>
SLUG: <lowercase-hyphenated-url-slug, no file extension>
DESCRIPTION: <one sentence, for the meta description and homepage listing>
PERSONA: <exactly one of: fitness-coaches, music-teachers, consultants, cohort-creators>
AFFILIATE_PROGRAM: <program name, or "TBD - pending application" if not yet confirmed>
BODY:
<the full article body in markdown, no frontmatter, starting directly with the first paragraph>"""


def _pipeline_options() -> ClaudeAgentOptions:
    # WebFetch and WebSearch only — research and verification, nothing that writes, runs
    # commands, or touches git. The actual file write and git push are hand-written Python
    # below (_write_article_file, _git_commit_and_push), never something this call does
    # itself, same reasoning as _create_spend_request never being a model-invoked tool.
    return ClaudeAgentOptions(
        system_prompt=_PIPELINE_SYSTEM_PROMPT,
        permission_mode="dontAsk",
        setting_sources=[],
        strict_mcp_config=True,
        disallowed_tools=["Write", "Edit", "MultiEdit", "NotebookEdit", "Bash",
                          "Read", "Grep", "Glob",
                          "Task", "Agent", "TodoWrite", "SlashCommand", "Skill",
                          "BashOutput", "KillShell", "ExitPlanMode",
                          "EnterPlanMode", "AskUserQuestion", "Artifact",
                          "ToolSearch", "Monitor", "EnterWorktree",
                          "ExitWorktree"],  # WebFetch and WebSearch intentionally absent
        max_turns=int(os.environ.get("PIPELINE_MAX_TURNS", "20")),
        model=os.environ.get("AGENT_MODEL", "claude-sonnet-5"),
        effort=os.environ.get("AGENT_EFFORT", "medium"),
    )


def _existing_slugs() -> list[str]:
    if not ARTICLES_DIR.exists():
        return []
    return sorted(f.stem for f in ARTICLES_DIR.glob("*.md") if not f.stem.startswith("_"))


async def _run_pipeline_research() -> str:
    existing = _existing_slugs()
    prompt = (f"Existing article slugs (do not repeat these topics): "
              f"{', '.join(existing) if existing else '(none yet)'}\n\n"
              f"Research and write one new article now.")
    parts: list[str] = []
    async with ClaudeSDKClient(options=_pipeline_options()) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
    return "\n".join(parts)


_ARTICLE_FIELD_RE = re.compile(
    r"TITLE:\s*(?P<title>.+?)\n"
    r"SLUG:\s*(?P<slug>.+?)\n"
    r"DESCRIPTION:\s*(?P<description>.+?)\n"
    r"PERSONA:\s*(?P<persona>.+?)\n"
    r"AFFILIATE_PROGRAM:\s*(?P<affiliate>.+?)\n"
    r"BODY:\s*\n(?P<body>.*)",
    re.DOTALL,
)


def _parse_article(raw: str) -> dict | None:
    match = _ARTICLE_FIELD_RE.search(raw)
    if not match:
        return None
    fields = {k: v.strip() for k, v in match.groupdict().items()}
    slug = re.sub(r"[^a-z0-9-]", "", fields["slug"].lower().replace(" ", "-"))
    if not slug or not fields["body"]:
        return None
    return {
        "title": fields["title"],
        "slug": slug,
        "description": fields["description"],
        "persona": fields["persona"],
        "affiliate_program": fields["affiliate"],
        "body": fields["body"],
    }


def _write_article_file(article: dict) -> Path:
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    path = ARTICLES_DIR / f"{article['slug']}.md"
    # status is hardcoded to "draft" here, full stop -- this function has no parameter that
    # could set it to "published". That is the actual enforcement point for "the pipeline
    # never publishes itself", not a policy statement elsewhere that this code could drift
    # away from. See tests/test_bot.py for the mutation-check on this.
    frontmatter = (
        "---\n"
        f"title: \"{article['title']}\"\n"
        f"description: \"{article['description']}\"\n"
        f"persona: \"{article['persona']}\"\n"
        f"affiliateProgram: \"{article['affiliate_program']}\"\n"
        f"publishedAt: \"{article.get('date', '')}\"\n"
        "status: \"draft\"\n"
        "---\n\n"
    )
    path.write_text(frontmatter + article["body"] + "\n")
    return path


def _git_commit_and_push(paths: list[str], message: str) -> tuple[bool, str]:
    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(REPO_ROOT), *args],
                              capture_output=True, text=True, timeout=30)

    add = run("add", *paths)
    if add.returncode != 0:
        return False, f"git add failed: {add.stderr.strip()}"

    commit = run("commit", "-m", message)
    if commit.returncode != 0:
        return False, f"git commit failed: {commit.stderr.strip()}"

    if not GITHUB_TOKEN:
        return False, "written and committed locally, but GITHUB_TOKEN isn't set — can't push."

    remote_url = f"https://x-access-token:{GITHUB_TOKEN}@github.com/2Rds/Autonomous-Venture.git"
    push = run("push", remote_url, "HEAD:main")
    if push.returncode != 0:
        # Never let a failed push leave the token in a message this process might surface.
        stderr = push.stderr.replace(GITHUB_TOKEN, "***")
        return False, f"git commit succeeded but push failed: {stderr.strip()}"
    return True, "pushed"


async def _run_content_cycle() -> str:
    raw = await _run_pipeline_research()
    article = _parse_article(raw)
    if article is None:
        log.warning("pipeline output did not parse as an article")
        return "Pipeline ran but the output didn't parse as an article, nothing written. Raw output logged."
    article["date"] = date.today().isoformat()
    path = _write_article_file(article)
    rel_path = str(path.relative_to(REPO_ROOT))
    ok, push_status = _git_commit_and_push(
        [rel_path],
        f"Pipeline draft: {article['title']}\n\nAuto-generated by the content pipeline, "
        f"status: draft. Needs a human read before publishing.",
    )
    status_line = "pushed to GitHub" if ok else push_status
    return (f"New draft: **{article['title']}** ({article['slug']})\n"
            f"{article['description']}\n"
            f"{status_line}. `drafts` won't show this one — it's a site article, not an "
            f"email/application draft; check {rel_path} directly.")


async def _handle_pipeline() -> str:
    result = await _run_content_cycle()
    # Stamped here too, not just in the scheduled loop -- otherwise an on-demand run right
    # before the hourly check would leave last_pipeline_run stale and the loop would fire
    # again shortly after, duplicating the cycle.
    _state["last_pipeline_run"] = datetime.now(timezone.utc).isoformat()
    _save_state()
    return result


def _pipeline_due() -> bool:
    last = _state.get("last_pipeline_run")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    elapsed_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
    return elapsed_hours >= CONTENT_PIPELINE_INTERVAL_HOURS


async def content_pipeline_loop(app: Application) -> None:
    # Runs inside this same systemd service, not inside any interactive session -- this is
    # the actual durability the whole point of this feature rests on. An hour is per-tick
    # granularity for checking whether the interval has elapsed, not the interval itself --
    # _pipeline_due() is what actually gates a run.
    while True:
        await asyncio.sleep(3600)
        if _state.get("paused") or not _pipeline_due():
            continue
        try:
            result = await _run_content_cycle()
        except Exception:
            log.exception("scheduled content cycle failed")
            result = "Scheduled content cycle raised an exception, check the journal."
        _state["last_pipeline_run"] = datetime.now(timezone.utc).isoformat()
        _save_state()
        await _send_long(app.bot, OPERATOR_TELEGRAM_ID, result)


async def _send_long(bot, chat_id: int, text: str) -> None:
    # Telegram caps messages at 4096 chars; a full article body will exceed that.
    limit = 3500
    for i in range(0, len(text), limit):
        await bot.send_message(chat_id=chat_id, text=text[i:i + limit])


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
        f"last pipeline run: {_state.get('last_pipeline_run', 'never')}\n"
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
    if lowered == "pipeline":
        return await _handle_pipeline()

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
    await _send_long(context.bot, chat_id, reply)


_pipeline_task: asyncio.Task | None = None


async def _on_startup(app: Application) -> None:
    # Plain asyncio.create_task, not Application.create_task: post_init runs during
    # initialize(), before PTB considers itself "running", and Application.create_task warns
    # (PTBUserWarning) that a task created before that point "won't be automatically awaited"
    # by its own shutdown handling.
    global _pipeline_task
    _pipeline_task = asyncio.create_task(content_pipeline_loop(app), name="content_pipeline_loop")
    log.info("content pipeline loop scheduled (every %.0fh)", CONTENT_PIPELINE_INTERVAL_HOURS)


async def _on_shutdown(app: Application) -> None:
    # Without this, stopping the app (including every restart) destroys the loop's task
    # mid-sleep and asyncio logs "Task was destroyed but it is pending!" as an ERROR on every
    # single restart -- harmless (systemd's SIGTERM ends the process either way) but noisy
    # enough to look like a real fault when reading the journal later. Cancelling it here
    # first gives it a clean exit instead.
    if _pipeline_task is not None:
        _pipeline_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _pipeline_task


def build_app() -> Application:
    app = (Application.builder().token(TELEGRAM_BOT_TOKEN)
           .post_init(_on_startup).post_shutdown(_on_shutdown).build())
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
