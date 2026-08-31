# Automated Revenue Plan — Content → Recurring Affiliate Engine

Status: niche and mechanism decided autonomously (per your "you choose" instruction on 2026-08-30).
Nothing external has been created yet — no domain, no hosting account, no affiliate applications.
Everything below is either done (research) or a proposed next action waiting on you for the parts
that need money or your identity.

## Why this shape (recap)

Affiliate/content monetization has no support queue, no billing disputes, no customer who can page
you. Once the content engine and monetization are wired, the loop is generate → publish → get paid.
A SaaS or consulting income needs you (or a hired human) in the loop forever — this doesn't, once built.

## Niche decision: rejected one, chose another — here's why

**Rejected: AI coding-assistant tools ("Claude Code vs Cursor vs Copilot vs Windsurf").**
Killed on evidence, two ways: search results returned 9+ near-identical comparison sites already
covering it (unrankable for a new domain), and neither Anthropic nor Cursor appear to run a public
affiliate program (unmonetizable even if it ranked).

**Rejected: cold email / outbound automation / GTM tooling (e.g. an Instantly.ai affiliate site).**
This one had real numbers behind it — Instantly's affiliate program pays up to 40% recurring, and I
initially planned to build on it. Stopping this one wasn't a competition or monetization problem, it
was a conflict of interest I should have caught before researching it: **cold email/outbound is
AgentCorp's own market.** AgentCorp sells outbound/GTM agents (Sam-Sales) into that exact ICP, and my
plan had drifted into reusing the company's own `agentcorp-cold-email` and `agentcorp-list-ops`
playbooks as content source material — those are IP checked into the company repo, built with your
cofounder. Whether a personal site in AgentCorp's own market, funded by AgentCorp's own playbooks, is
fine is a real question — but it's an ownership question with a second person attached, not a call
"minimal input from me" was meant to authorize. So: **avoided, not built.** If you actually want this
version (it's the highest-EV one I found), say so explicitly and we can revisit — possibly as a
company-owned asset instead of a personal one.

**Chosen: tools for online course creators & coaches (Kajabi / Teachable / Podia / Thinkific
ecosystem).**
- Real, standard recurring affiliate programs (Kajabi and similar typically run ~20-30% recurring
  through their own or PartnerStack-style programs) — not vendor-blog-only claims; these are
  long-established programs with public terms.
- Zero overlap with AgentCorp's ICP (B2B buyers of AI employee-agents) and needs none of its IP —
  content here is genuinely researchable from each tool's own docs/pricing/feature pages.
- Narrow personas available for long-tail targeting instead of fighting head terms: e.g. "for fitness
  coaches," "for music teachers," "for consultants," "for cohort-based course creators" — this is
  where a new domain can actually rank; "best course platform 2026" outright cannot.

**Not yet verified, and flagged rather than assumed:** affiliate programs in this space are often
gated behind an application (PartnerStack, Impact, or the vendor's own review), same as Instantly's
was. Approval is not guaranteed for any single one. Mitigation built into the plan below: apply to
5-8 programs across the ecosystem in Phase 1, not one, so the site isn't dependent on a single
approval decision.

## Honest arithmetic — what $10k/mo actually requires

The number I quoted earlier ("$10K-$20K/month," from an affiliate-marketing blog citing the vendor's
own program page) is the least reliable class of figure available and I shouldn't have repeated it
uncritically. Real shape, using this niche's plausible numbers:

- Typical plan: ~$40-100/mo. Typical recurring rate: ~20-30%.
- That's roughly **$10-30 per month per retained paying referral.**
- To reach $10k/mo you need on the order of **350-650 simultaneously active, paying, retained
  referrals** — not signups, not clicks, not one-time approvals. People who are still paying months
  later.
- SEO content ramps over **6-18 months** before meaningful organic traffic arrives, and affiliate
  conversion rates on cold organic traffic are typically low single-digit percent of visitors who even
  click the link, of whom a fraction convert, of whom a fraction stay subscribed past the trial.

**Realistic milestones, stated honestly instead of implied:**
- Months 1-3: build + publish, ~$0. This is infrastructure time.
- Months 4-9: first organic traffic and first affiliate approvals land; realistic revenue is
  low-hundreds to ~$1-1.5k/mo from the first site if the niche and content quality are right.
- $10k/mo is a **portfolio outcome**, not a single-site outcome: it needs either 3-5 sites across
  different niches run on the same engine, or this site plus a display-ad layer once traffic is
  substantial (tens of thousands of monthly visits), or both. Phase 6 below reflects this — treat the
  first site as proof-of-mechanism, not as the whole plan.

This is still worth building — the marginal cost of site #2 and #3 on a working engine is setup time,
not ongoing time — but the plan should not promise $10k/mo from one small site in a few months, because
that isn't true.

## Phase 0 — Niche & positioning (done, this session)

Chosen niche: course-creator/coaching tool reviews and tutorials, persona-narrowed. No further input
needed to proceed to Phase 1 unless you want to override the niche choice above.

## Phase 1 — Infrastructure (needs you: money + identity, ~1 day of work once unblocked)

Cannot be done autonomously — needs a payment method and your identity on file:
- [ ] Register a domain (~$12/yr).
- [ ] Deploy hosting (Vercel free tier covers this at low traffic — may already have an account).
- [ ] Apply to 5-8 affiliate programs in the niche (Kajabi, Teachable, Podia, Thinkific, and 2-3
      adjacent tools — email/booking/community tools course creators also use). Applications ask for
      your existing web presence, which won't exist yet on day one; expect to reapply once the site
      has a few articles live.
- [ ] Connect the domain to SearchFit as a brand once it exists, wire up Search Console + GA4.

I can do all of the surrounding work (scaffolding, content, automation) without these, but the site
can't go live or earn until this phase happens.

## Phase 2 — Content engine (can start now, no blockers)

- Keyword research via SearchFit (`discover_keywords`, `enrichment_generate_filters`) once a brand
  exists — this is the actual competition check, not guesswork: it scores real difficulty/volume, and
  is how narrow, rankable long-tail queries get found within the niche.
- Draft via `generate_topic_suggestions` → `bulk_create_content`, run every draft through the
  human-writing skill before it's queued (the single highest-leverage QA step — AI-toned content is
  the main reason automated content sites get filtered out of search and AI answer engines).
- **Ongoing sampling QA, not a one-time gate.** Original draft of this plan said "drop the approval
  gate after ~20 articles" — that's wrong: Google's scaled-content-abuse policy targets exactly
  "publish content and stop checking it" patterns. Instead: review 100% of the first ~20 articles,
  then keep reviewing a random 15-20% sample every month indefinitely, plus 100% of anything the
  automated QA below flags.

## Phase 3 — Automate the loop

**Built 2026-08-31, on `agent/bot.py`, not as originally planned.** This phase originally said
"`CronCreate` scheduled cloud run" — that tool is session-only, tied to an interactive Claude
Code session, and gone when the session ends. Not durable, and durability was the actual
requirement ("I want true autonomy... without me needing to be prompted"). What's actually
running: a scheduled loop (`content_pipeline_loop`) inside the same systemd service as the
Telegram bot, on its own droplet, `Restart=always`. It researches one new article via
WebSearch/WebFetch, verifies every claim against a live source the same way the first article
was written by hand, writes it to `site/content/articles/` as `status: draft` (hardcoded, no
parameter path to anything else, mutation-tested), and commits + pushes it. Publishing is still
a human action — the loop only ever produces drafts. `pipeline` command runs one cycle
on demand; the interval is `CONTENT_PIPELINE_INTERVAL_HOURS` (default weekly).

SearchFit keyword refresh from the original plan is dropped, not deferred: adding a
CreatorStacked brand would put it in AgentCorp's shared SearchFit workspace (same entanglement
problem as the droplet/DO account earlier), and Sean's SearchFit trial expired anyway. Direct
WebSearch/WebFetch research is the substitute and is what the first article was actually built
with.

Not yet built: an automated report (SearchFit or PostHog needed a working connection for that;
see above) and updating underperforming existing articles rather than only adding new ones.

## Phase 4 — Monetization wiring

- Affiliate links inserted at the content-template level, not per-article by hand.
- Dead-link/dead-program check monthly — affiliate programs get discontinued silently; a stale link
  is silent revenue loss with no error to notice.

## Phase 5 — Guardrails

- Traffic/rank regression alert (threshold check in the cron job) — the only thing that should ever
  actually interrupt you.
- Quarterly manual read of a content sample against Google's scaled-content-abuse and affiliate
  content policies, since both evolve.

## Phase 6 — Scale toward $10k/mo (portfolio, not single-site)

Once site #1's engine is proven (real traffic, real approved-and-paying affiliates, content passing
the sampling QA), replicate the engine into 2-4 more niches picked with the same test used here: real
recurring affiliate program with public terms, zero ICP overlap with AgentCorp, narrow enough to rank.
Each additional site costs setup time on a reusable engine, not new ongoing effort.

## Progress log

- **2026-08-30:** Site scaffolded at `./site` — Next.js 16 (App Router, TS, Tailwind). Content
  pipeline wired: `content/articles/*.md` (frontmatter: title, description, persona,
  affiliateProgram, publishedAt, status) → parsed via `gray-matter` → rendered via `remark` →
  served at `/articles/[slug]`, with drafts excluded from the home listing but reachable by direct
  URL for review. `AffiliateLink` component enforces `nofollow sponsored` on every outbound link;
  `DisclosureBanner` renders the FTC affiliate disclosure on every article. `_template.md` documents
  the fact-check requirement before any article can flip to `status: published`. `npm run build`
  verified clean (confirms the whole chain — markdown parse through static page generation — actually
  works, not just that the files exist). No content has been written yet; `_template.md` is
  deliberately excluded from the build.

- **2026-08-31:** Agent rebuilt as a Python/`claude_agent_sdk` service on a dedicated droplet
  (see `agent/README.md`), Max-plan auth, Telegram interface live. Added `draft`/`send` for
  email (AgentMail, live-verified) and affiliate-program applications — with no send/submit path
  for applications at all, ever, matching the boundary below.

- **2026-08-31: `creatorstacked.com` registered at Porkbun.** Worth recording why this took three
  spend-request cycles and ended with Sean doing the actual checkout, not the agent: the Link
  spend-request flow authorizes *money*, but completing a registrar purchase also means creating
  an account there (email, ToS, WHOIS contact) — the same category of action already ruled out
  for the affiliate-program applications earlier in this doc. That wasn't obvious until Sean
  pushed on it directly ("get me there in a chrome browser") and the inconsistency surfaced: the
  original plan described "get the card, then complete checkout via browser automation" without
  having worked through that checkout *is* account creation. Corrected mid-session rather than
  left inconsistent. Going forward: the agent can request spend and can navigate a browser to a
  page, but signing up for anything third-party stays Sean's action, full stop — see
  `agent/README.md` "Trust ladder" for where this is enforced in code (the `draft application`
  path has no `send`).

- **2026-08-31: Telegram Mini App dashboard added** (`site/src/app/dashboard`, `site/src/app/api/
  miniapp/`, `agent/bot.py` `app` command). Covers status, spend-request visibility, and draft
  article review/approve/edit/reject. Two decisions worth recording:
  - **Status store is Upstash Redis, not AgentCorp's Redis Cloud.** Sean initially proposed a
    separate instance under AgentCorp's own Redis Cloud account; on reflection that still crosses
    the infra-separation boundary this venture was set up with (AgentCorp's account, even for one
    unrelated instance, is still AgentCorp's infra). `2Rds/Autonomous-Venture` is also a public
    repo, so spend-request amounts/merchants and pause state can't be git-backed either — Upstash
    (via Vercel's marketplace integration) keeps that data out of both. The store Sean created was
    already connected to the `creatorstacked` project on creation (confirmed live in the Vercel
    dashboard, not assumed), and Vercel's native Upstash integration injects `KV_REST_API_URL`/
    `KV_REST_API_TOKEN` (legacy Vercel KV naming), not `UPSTASH_REDIS_REST_URL`/`_TOKEN` as
    originally coded — `agent/bot.py` and the dashboard's status route read the former.
  - **No lock needed around the pipeline's write+commit+push.** Initially added an `asyncio.Lock`
    around it on the theory that the mini app's draft approve/edit/reject (writing to `main` via
    GitHub's Contents API, bypassing the droplet's local clone) could let two `_run_content_cycle`
    calls interleave their git subprocess calls. Mutation-tested that assumption directly: with the
    lock removed, a concurrency test using two overlapping cycles and a mocked commit function
    still showed zero interleaving, because the whole write+commit+push sequence has no `await`
    inside it — asyncio can't switch tasks mid-sequence without a yield point. Removed the lock
    rather than keep code whose own justifying comment didn't hold up. What IS real and kept: a
    `git fetch && git merge --ff-only origin/main` at the start of `_git_commit_and_push`, so the
    droplet's local clone doesn't fall behind commits the mini app made directly via the API.

## What's next, concretely

I can proceed right now, with no further input, on:
- Scaffolding the actual site codebase (Next.js) in this folder.
- Drafting the first batch of long-tail topic candidates by persona (fitness coaches, music teachers,
  consultants, cohort-course creators) for you to sanity-check once real keyword data is wired in.

I cannot proceed without you on:
- Domain purchase and hosting account (payment method).
- Affiliate program applications (your identity/business details).
- A decision on whether the cold-email/outbound niche should actually happen as a **company-owned**
  asset instead — flagging it here since it was the highest-EV option found, just not one I should
  greenlight unilaterally.
