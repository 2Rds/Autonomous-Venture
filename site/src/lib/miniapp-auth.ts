import { NextResponse } from "next/server";
import { requireOperator } from "./telegram-auth";

// The Mini App frontend sends Telegram's window.Telegram.WebApp.initData verbatim in this
// header on every request. Every miniapp API route calls this first and returns immediately
// on failure -- there is no route in this tree that trusts the frontend without it, including
// the read-only ones, since this is a single-operator system with nothing to gain from being
// lenient about who's asking.
const INIT_DATA_HEADER = "x-telegram-init-data";

export function authenticateMiniapp(
  req: Request
): { ok: true; userId: number } | { ok: false; response: NextResponse } {
  const botToken = process.env.TELEGRAM_BOT_TOKEN ?? "";
  const operatorIdRaw = process.env.OPERATOR_TELEGRAM_ID ?? "";
  const operatorId = parseInt(operatorIdRaw, 10);

  if (!botToken || !operatorIdRaw || Number.isNaN(operatorId)) {
    return {
      ok: false,
      response: NextResponse.json(
        { error: "server misconfigured: TELEGRAM_BOT_TOKEN / OPERATOR_TELEGRAM_ID not set" },
        { status: 500 }
      ),
    };
  }

  const initData = req.headers.get(INIT_DATA_HEADER) ?? "";
  const result = requireOperator(initData, botToken, operatorId);
  if (!result.ok || result.userId === undefined) {
    return {
      ok: false,
      response: NextResponse.json({ error: result.reason ?? "unauthorized" }, { status: 401 }),
    };
  }
  return { ok: true, userId: result.userId };
}

export { INIT_DATA_HEADER };
