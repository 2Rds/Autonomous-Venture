import { NextResponse } from "next/server";
import matter from "gray-matter";
import { authenticateMiniapp } from "@/lib/miniapp-auth";
import { deleteArticleFile, getArticleFile } from "@/lib/github-content";

export async function POST(
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
  const parsed = matter(file.content);
  if (parsed.data.status !== "draft") {
    return NextResponse.json(
      { error: `article status is "${parsed.data.status}", not "draft", so reject only removes unpublished drafts` },
      { status: 409 }
    );
  }

  await deleteArticleFile(
    token,
    slug,
    file.sha,
    `Reject draft: ${parsed.data.title ?? slug}\n\nRejected via the CreatorStacked dashboard.`
  );

  return NextResponse.json({ ok: true, slug });
}
