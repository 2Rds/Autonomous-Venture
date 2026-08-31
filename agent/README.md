# CreatorStacked agent

Cloudflare Worker: a Telegram bot for talking to the CreatorStacked agent, plus a daily cron
stub. The actual content pipeline (keyword research, drafting, publishing) is not in here —
see `../PLAN.md` Phase 3 for why.

## Setup (not done yet — needs you)

1. Create the bot with [@BotFather](https://t.me/BotFather) on Telegram (`/newbot`), get the token.
2. Pick a random string for `TELEGRAM_WEBHOOK_SECRET` (e.g. `openssl rand -hex 32`).
3. `npm install`
4. `npx wrangler deploy` — deploys the Worker, prints its URL.
5. Set secrets:
   ```
   npx wrangler secret put TELEGRAM_BOT_TOKEN
   npx wrangler secret put TELEGRAM_WEBHOOK_SECRET
   npx wrangler secret put ANTHROPIC_API_KEY
   ```
6. Register the webhook with Telegram:
   ```
   curl "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     -d "url=https://<worker-url>/telegram-webhook" \
     -d "secret_token=<same value as TELEGRAM_WEBHOOK_SECRET>"
   ```

## Local dev

`cp .dev.vars.example .dev.vars`, fill it in (never commit `.dev.vars` — it's gitignored), then
`npm run dev`.
