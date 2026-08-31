# Autonomous Venture

Personal, separate from AgentCorp — see `PLAN.md` for why that separation matters and what this
actually is (automated content + recurring-affiliate site for course-creator/coaching tools).

- `site/` — the public site (Next.js). Content pipeline wired, no content published yet.
- `agent/` — Telegram interface to the agent: Python + `claude_agent_sdk`, authenticated via
  Sean's Claude Max subscription (`CLAUDE_CODE_OAUTH_TOKEN`), same pattern as the rest of the
  `2Rds/agents` fleet. Runs on its own droplet (not colocated with AgentCorp's), not Cloudflare
  Workers — the Agent SDK spawns the `claude` CLI as a subprocess, which Workers' V8 isolate
  can't do. See `agent/README.md` for the trust ladder and setup runbook.
- `PLAN.md` — the plan, the niches rejected and why, the honest revenue math, and the scope
  boundaries the agent operates under.

## Status as of 2026-08-30

- Domain (`creatorstacked.com`) purchase is pending — a Link spend-request is awaiting approval,
  nothing charged yet.
- Site scaffold builds clean, zero content written.
- Agent: code written, tests pass (16/16), guards mutation-verified. Not deployed — waiting on a
  free droplet slot on the DO account (at its 3-droplet cap, consolidation in progress), a
  Telegram bot token, and `CLAUDE_CODE_OAUTH_TOKEN`. None of those exist yet.
