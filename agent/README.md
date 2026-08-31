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
- **`pause` is a real kill switch.** It gates both `spend` and free-form chat; `status`,
  `pause`, `resume`, `help` always work regardless.
- **Next rung, not built yet:** letting the chat model decide to create spend-requests on its
  own mid-conversation (an in-process tool wrapping `_create_spend_request`) instead of waiting
  for the operator to type `spend`. That's a deliberate later step once this stage is proven,
  not an oversight.

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
   your identity being authenticated.
5. On the droplet, `/opt/creatorstacked/agent`:
   ```
   git clone <this repo> .   # or git pull if already cloned
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```
6. Write `.env` from `.env.example` (paste secrets on a real TTY), `chown agentbot`,
   `chmod 600 .env`.
7. `mkdir .data && chown agentbot .data` — must exist before first start (the unit's
   `ReadWritePaths` lists it).
8. Install + enable `systemd/creatorstacked-bot.service`.
9. Smoke test: DM the bot `status` — should report the repo's git SHA and any open
   spend-requests (none, at first).

## Local dev

`cp .env.example .env`, fill it in, `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`, `pytest`, then `python bot.py`.
