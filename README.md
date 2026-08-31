# Autonomous Venture

Personal, separate from AgentCorp — see `PLAN.md` for why that separation matters and what this
actually is (automated content + recurring-affiliate site for course-creator/coaching tools).

- `site/` — the public site (Next.js). Content pipeline wired, no content published yet.
- `agent/` — Cloudflare Worker: the Telegram interface to the agent, plus a cron stub. See
  `agent/README.md` for setup (needs a Telegram bot token and an Anthropic API key — neither
  exists yet).
- `PLAN.md` — the plan, the niches rejected and why, the honest revenue math, and the scope
  boundaries the agent operates under.

## Status as of 2026-08-30

- Domain (`creatorstacked.com`) purchase is pending — a Link spend-request is awaiting approval,
  nothing charged yet.
- Site scaffold builds clean, zero content written.
- Agent Worker type-checks and bundles clean, not deployed — no bot token or Telegram webhook
  exists yet.
