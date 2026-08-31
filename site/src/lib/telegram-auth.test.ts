import { test } from "node:test";
import assert from "node:assert/strict";
import crypto from "crypto";
import { verifyInitData, requireOperator } from "./telegram-auth";

const BOT_TOKEN = "123456:test-bot-token-not-real";

// Telegram doesn't publish official test vectors, so this builds a valid initData string the
// same way Telegram itself would, using the documented algorithm -- the same one
// verifyInitData implements. That's the only way to test the accept path without a live bot.
function buildInitData(fields: Record<string, string>, signingToken = BOT_TOKEN): string {
  const dataCheckString = Object.entries(fields)
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
  const secretKey = crypto.createHmac("sha256", "WebAppData").update(signingToken).digest();
  const hash = crypto.createHmac("sha256", secretKey).update(dataCheckString).digest("hex");
  return new URLSearchParams({ ...fields, hash }).toString();
}

function validFields(overrides: Partial<Record<string, string>> = {}) {
  return {
    auth_date: Math.floor(Date.now() / 1000).toString(),
    user: JSON.stringify({ id: 42, first_name: "Sean" }),
    ...overrides,
  };
}

test("accepts a correctly signed initData string and extracts the user id", () => {
  const result = verifyInitData(buildInitData(validFields()), BOT_TOKEN);
  assert.equal(result.ok, true);
  assert.equal(result.userId, 42);
});

test("rejects initData with a tampered field", () => {
  const initData = buildInitData(validFields()).replace("Sean", "Mallory");
  const result = verifyInitData(initData, BOT_TOKEN);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "hash mismatch");
});

test("rejects a stale auth_date even with a valid signature", () => {
  const twoDaysAgo = Math.floor(Date.now() / 1000) - 2 * 24 * 60 * 60;
  const result = verifyInitData(
    buildInitData(validFields({ auth_date: twoDaysAgo.toString() })),
    BOT_TOKEN
  );
  assert.equal(result.ok, false);
  assert.equal(result.reason, "stale auth_date");
});

test("rejects a string signed with a different bot token", () => {
  const initData = buildInitData(validFields(), "some-other-token");
  const result = verifyInitData(initData, BOT_TOKEN);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "hash mismatch");
});

test("rejects a missing hash", () => {
  const params = new URLSearchParams(validFields());
  const result = verifyInitData(params.toString(), BOT_TOKEN);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "missing hash");
});

test("requireOperator accepts only the configured operator id", () => {
  const initData = buildInitData(validFields());
  assert.equal(requireOperator(initData, BOT_TOKEN, 42).ok, true);
  const wrongOperator = requireOperator(initData, BOT_TOKEN, 999);
  assert.equal(wrongOperator.ok, false);
  assert.equal(wrongOperator.reason, "not operator");
});
