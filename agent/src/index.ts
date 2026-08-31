import { SYSTEM_PROMPT } from "./scope";
import { askClaude } from "./claude";
import { sendTelegramMessage, type TelegramUpdate } from "./telegram";

export interface Env {
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  ANTHROPIC_API_KEY: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return new Response("ok");
    }

    if (url.pathname === "/telegram-webhook" && request.method === "POST") {
      const secret = request.headers.get("x-telegram-bot-api-secret-token");
      if (secret !== env.TELEGRAM_WEBHOOK_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }

      const update = (await request.json()) as TelegramUpdate;
      const chatId = update.message?.chat.id;
      const text = update.message?.text;

      if (chatId && text) {
        try {
          const reply = await askClaude(env.ANTHROPIC_API_KEY, SYSTEM_PROMPT, text);
          await sendTelegramMessage(env.TELEGRAM_BOT_TOKEN, chatId, reply);
        } catch (err) {
          console.error("telegram-webhook handling failed", err);
          await sendTelegramMessage(
            env.TELEGRAM_BOT_TOKEN,
            chatId,
            "Something went wrong handling that — check `wrangler tail` for details."
          );
        }
      }

      // Telegram just needs a 200 to stop retrying; the actual work already happened above.
      return new Response("ok");
    }

    return new Response("not found", { status: 404 });
  },

  async scheduled(_controller: ScheduledController, _env: Env): Promise<void> {
    // Deliberately a stub. The real weekly content pipeline (keyword research, drafting,
    // sampling QA, publishing) runs as a scheduled Claude Code cloud agent against the
    // SearchFit MCP tools — see PLAN.md Phase 3 for why that lives there and not here.
    // This trigger exists so there's a place to add a lightweight daily health/metrics
    // check later, once there's real traffic/revenue data worth checking.
  },
};
