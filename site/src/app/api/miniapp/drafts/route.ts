import { NextResponse } from "next/server";
import matter from "gray-matter";
import { authenticateMiniapp } from "@/lib/miniapp-auth";
import { getArticleFile, listArticleFiles } from "@/lib/github-content";

// Reads via the GitHub Contents API, not local fs -- Vercel's filesystem for a serverless
// function doesn't reflect commits made after the last deploy (including ones the mini app
// itself just made through this same API), so this has to be live against the repo every time.
export async function GET(req: Request) {
  const auth = authenticateMiniapp(req);
  if (!auth.ok) return auth.response;

  const token = process.env.GITHUB_TOKEN ?? "";
  if (!token) {
    return NextResponse.json({ error: "GITHUB_TOKEN not configured" }, { status: 503 });
  }

  const files = await listArticleFiles(token);
  const drafts = (
    await Promise.all(
      files.map(async ({ slug }) => {
        const file = await getArticleFile(token, slug);
        if (!file) return null;
        const { data } = matter(file.content);
        if (data.status !== "draft") return null;
        return {
          slug,
          title: data.title ?? slug,
          description: data.description ?? "",
          persona: data.persona ?? "",
          affiliateProgram: data.affiliateProgram ?? "",
          publishedAt: data.publishedAt ?? "",
        };
      })
    )
  ).filter((d): d is NonNullable<typeof d> => d !== null);

  return NextResponse.json({ drafts });
}
