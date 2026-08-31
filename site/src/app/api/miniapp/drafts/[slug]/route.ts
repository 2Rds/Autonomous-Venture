import { NextResponse } from "next/server";
import matter from "gray-matter";
import { authenticateMiniapp } from "@/lib/miniapp-auth";
import { getArticleFile } from "@/lib/github-content";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> }
) {
  const auth = authenticateMiniapp(req);
  if (!auth.ok) return auth.response;

  const token = process.env.GITHUB_TOKEN ?? "";
  if (!token) {
    return NextResponse.json({ error: "GITHUB_TOKEN not configured" }, { status: 503 });
  }

  const { slug } = await params;
  const file = await getArticleFile(token, slug);
  if (!file) {
    return NextResponse.json({ error: "no such article" }, { status: 404 });
  }
  const { data, content } = matter(file.content);
  return NextResponse.json({ slug, ...data, body: content.trim() });
}
