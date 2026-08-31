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

## Status as of 2026-08-31

- **`creatorstacked.com` is registered** (Porkbun) — purchased by Sean directly, not automated;
  see PLAN.md "Progress log" for why domain-registrar signup falls under the same
  account-creation boundary as the affiliate-program applications.
- Site scaffold builds clean, zero content written, **not deployed anywhere yet** — the domain
  currently points at nothing. Next real step: pick a host (Vercel was the working assumption in
  PLAN.md Phase 1) and point DNS at it.
- Agent: live and running on its own droplet (`creatorstacked-agent`), Telegram bot responding,
  30/30 tests pass, guards mutation-verified, send pipeline (AgentMail) live-verified against the
  real API. `draft`/`send`/`spend`/`status`/`pause`/`resume` all working.
