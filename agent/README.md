# CreatorStacked agent

Telegram interface to the CreatorStacked agent, following the same conventions as the rest of
the `2Rds/agents` fleet (`daily-brief`, `babysitter`, etc.) — Python, `claude_agent_sdk`,
`CLAUDE_CODE_OAUTH_TOKEN` (Max-subscription auth), systemd on a droplet, operator allow-list,
kill switch. See `../PLAN.md` for the business plan and scope this agent operates under.

**Not Cloudflare Workers, on purpose.** An earlier version of this was a Worker calling the
Anthropic API with a separate API key. That doesn't connect to Sean's Max plan the way the rest
of the fleet does — the Agent SDK works by spawning the actual `claude` CLI as a subprocess,
which a Workers V8 isolate cannot do. Matching the fleet's auth meant matching its runtime.

## Trust ladder — what this bot can and can't do today

- **Chat is read-only.** The Claude call has zero tool access (`disallowed_tools` covers every
  built-in, `setting_sources=[]`, `strict_mcp_config=True` — see `_claude_options()` in
  `bot.py`). It can discuss the plan and reason out loud; it cannot read files, run commands,
  or touch money from a free-form message.
- **Spending is a fixed operator command, not a model-invoked tool.** `spend` is hand-written
  Python that shells out to `link-cli spend-request create` — deliberately not registered as a
  tool the chat model can call itself. Every purchase this bot ever requests traces back to an
  explicit `spend` command, and even then nothing is charged until Sean approves it himself in
  the Link app.
- **Drafting is a separate, WebFetch-only Claude call — `_draft_options()`, not
  `_claude_options()`.** `draft email` and `draft application` can look up a real page to
  ground what they write, but still can't write a file, run a command, or touch an MCP server.
  Every other tool stays disallowed regardless of what the draft needed to look up.
- **Sending is not the same trust level as drafting, and the two commands are not symmetric
  on purpose.** `send <id>` sends an `email`-kind draft via AgentMail — but only after the
  draft has been shown to Sean here first, and only him deciding to send it. There is **no
  send path at all for `application`-kind drafts** — `_handle_send` refuses them
  unconditionally, every time, regardless of approval. Submitting an account or application on
  a third-party platform is not something this process automates at any trust stage; Sean
  copies the draft into the form himself. This is a hard line, not a staged one — see
  `../PLAN.md` "Progress log" for why.
- **`pause` is a real kill switch.** It gates `spend`, `draft`, `send`, `pipeline`, and
  free-form chat; `status`, `drafts`, `pause`, `resume`, `help` always work regardless.
- **The content pipeline (`pipeline` command, and `content_pipeline_loop` on a schedule) is
  where the real autonomy lives, and it's still bounded the same way.** A WebFetch+WebSearch
  Claude call (`_pipeline_options()`) researches one new article and writes it in the required
  `TITLE:`/`SLUG:`/.../`BODY:` format; hand-written Python (`_write_article_file`) is the only
  thing that turns that into a file, and it hardcodes `status: "draft"` with no parameter path
  to anything else (see the mutation-checked test
  `test_write_article_file_ignores_any_status_the_caller_tries_to_set`). Hand-written
  `_git_commit_and_push` commits and pushes the draft; it never touches `main` in a way that
  publishes anything, because nothing in this repo goes live off a commit alone. Runs inside
  this same systemd service, not inside any interactive Claude Code session, which is the
  actual point: it keeps running whether or not anyone is watching.
- **Next rung, not built yet:** letting the chat model decide to create spend-requests or
  drafts on its own mid-conversation (in-process tools) instead of waiting for the operator to
  type a command, or the pipeline ever proposing a `status: published` flip itself instead of
  Sean doing that by hand. Both are deliberate later steps once this stage is proven, not
  oversights.

## Setup (needs you — none of this exists yet)

1. **Droplet**: once a slot is free on the AgentCorp DO team account, create a small droplet
   (1GB is plenty) dedicated to this — not colocated with `waas-redis-nyc1` or the other fleet
   agents, to keep this venture's infra separate from AgentCorp's.
2. **Telegram bot**: [@BotFather](https://t.me/BotFather) → `/newbot` → token. Message
   [@userinfobot](https://t.me/userinfobot) to get your own numeric Telegram user ID for
   `OPERATOR_TELEGRAM_ID`.
3. **Claude Max auth**: on a machine where you're logged into Claude Code with your Max
   subscription, run `claude setup-token` → put the result in `CLAUDE_CODE_OAUTH_TOKEN`.
4. **`link-cli` on the droplet**: install `@stripe/link-cli` and authenticate it there —
   it has its own session, separate from the one on your laptop. I can't do this step; it's
   your identity being authenticated. Use an isolated Link account scoped to one card, not
   your personal wallet.
5. **AgentMail** (optional — only needed for `send`, not for `draft`): create the inbox, get
   an API key and the inbox's `inbox_id` from the dashboard (not necessarily the email address
   itself) for `AGENTMAIL_API_KEY` / `AGENTMAIL_INBOX_ID`.
6. **`GITHUB_TOKEN`** (optional — the content pipeline still researches and writes articles
   locally without it, it just can't push them anywhere you'd see them): a fine-grained PAT
   scoped to only `2Rds/Autonomous-Venture`, Contents: Read and write. Not a classic/full-access
   token.
7. On the droplet, `/opt/creatorstacked/agent`:
   ```
   git clone <this repo> .   # or git pull if already cloned
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
7. Write `.env` from `.env.example` (paste secrets on a real TTY), `chown agentbot`,
   `chmod 600 .env`.
7. `mkdir .data && chown agentbot .data` — must exist before first start (the unit's
   `ReadWritePaths` lists it).
8. Install + enable `systemd/creatorstacked-bot.service`.
9. Smoke test: DM the bot `status` — should report the repo's git SHA and any open
   spend-requests (none, at first).

## Local dev

`cp .env.example .env`, fill it in, `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`, `pytest`, then `python bot.py`.
