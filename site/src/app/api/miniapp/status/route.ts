import { NextResponse } from "next/server";
import { authenticateMiniapp } from "@/lib/miniapp-auth";

// Never reads git for this -- the bot pushes this snapshot to Upstash directly (see
// agent/bot.py _push_status_snapshot). Spend-request amounts and merchants live here, not in
// the public repo.
export async function GET(req: Request) {
  const auth = authenticateMiniapp(req);
  if (!auth.ok) return auth.response;

  const url = process.env.KV_REST_API_URL ?? "";
  const token = process.env.KV_REST_API_TOKEN ?? "";
  if (!url || !token) {
    return NextResponse.json({ error: "status store not configured yet" }, { status: 503 });
  }

  const resp = await fetch(`${url}/get/creatorstacked:status`, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!resp.ok) {
    return NextResponse.json(
      { error: `status store read failed: ${resp.status}` },
      { status: 502 }
    );
  }
  const data = await resp.json();
  if (!data.result) {
    return NextResponse.json({ error: "no status pushed yet" }, { status: 404 });
  }
  try {
    return NextResponse.json(JSON.parse(data.result));
  } catch {
    return NextResponse.json({ error: "status store returned invalid JSON" }, { status: 502 });
  }
}
