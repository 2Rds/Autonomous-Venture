import crypto from "crypto";

// Telegram invalidates initData it considers too old to trust for a fresh action; this bounds
// how long a captured initData string stays acceptable to this backend, independent of that.
const MAX_AUTH_AGE_SECONDS = 24 * 60 * 60;

export interface VerifyResult {
  ok: boolean;
  userId?: number;
  reason?: string;
}

/**
 * Verifies a Telegram Mini App `initData` string per Telegram's documented algorithm:
 * secret_key = HMAC_SHA256(key="WebAppData", data=botToken), then compare HMAC_SHA256(key=secret_key,
 * data=data_check_string) against the `hash` field. This is the only thing that proves a request
 * actually came from Telegram rather than an arbitrary HTTP client.
 */
export function verifyInitData(initData: string, botToken: string): VerifyResult {
  if (!initData) return { ok: false, reason: "empty initData" };
  if (!botToken) return { ok: false, reason: "server has no bot token configured" };

  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return { ok: false, reason: "missing hash" };
  params.delete("hash");

  const dataCheckString = [...params.entries()]
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([key, value]) => `${key}=${value}`)
    .join("\n");

  const secretKey = crypto.createHmac("sha256", "WebAppData").update(botToken).digest();
  const computedHash = crypto
    .createHmac("sha256", secretKey)
    .update(dataCheckString)
    .digest("hex");

  const computedBuf = Buffer.from(computedHash, "hex");
  const givenBuf = Buffer.from(hash, "hex");
  if (
    computedBuf.length !== givenBuf.length ||
    !crypto.timingSafeEqual(computedBuf, givenBuf)
  ) {
    return { ok: false, reason: "hash mismatch" };
  }

  const authDateStr = params.get("auth_date");
  const authDate = authDateStr ? parseInt(authDateStr, 10) : NaN;
  if (!authDateStr || Number.isNaN(authDate)) {
    return { ok: false, reason: "missing auth_date" };
  }
  const ageSeconds = Date.now() / 1000 - authDate;
  if (ageSeconds > MAX_AUTH_AGE_SECONDS) {
    return { ok: false, reason: "stale auth_date" };
  }

  const userRaw = params.get("user");
  if (!userRaw) return { ok: false, reason: "missing user" };
  let userId: unknown;
  try {
    userId = JSON.parse(userRaw)?.id;
  } catch {
    return { ok: false, reason: "invalid user JSON" };
  }
  if (typeof userId !== "number") return { ok: false, reason: "invalid user id" };

  return { ok: true, userId };
}

/** verifyInitData plus the single-operator check every miniapp route actually needs. */
export function requireOperator(
  initData: string,
  botToken: string,
  operatorId: number
): VerifyResult {
  const result = verifyInitData(initData, botToken);
  if (!result.ok) return result;
  if (result.userId !== operatorId) return { ok: false, reason: "not operator" };
  return result;
}
