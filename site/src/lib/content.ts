import fs from "fs";
import path from "path";
import matter from "gray-matter";
import { remark } from "remark";
import html from "remark-html";

const CONTENT_DIR = path.join(process.cwd(), "content", "articles");

export type ArticleStatus = "draft" | "published";

export interface ArticleMeta {
  slug: string;
  title: string;
  description: string;
  persona: string;
  affiliateProgram: string;
  publishedAt: string;
  status: ArticleStatus;
}

export interface Article extends ArticleMeta {
  contentHtml: string;
}

function readSlugs(): string[] {
  if (!fs.existsSync(CONTENT_DIR)) return [];
  return fs
    .readdirSync(CONTENT_DIR)
    .filter((file) => file.endsWith(".md") && !file.startsWith("_"))
    .map((file) => file.replace(/\.md$/, ""));
}

export function getAllArticleMeta(includeDrafts = false): ArticleMeta[] {
  const slugs = readSlugs();
  const all = slugs.map((slug) => {
    const fullPath = path.join(CONTENT_DIR, `${slug}.md`);
    const { data } = matter(fs.readFileSync(fullPath, "utf8"));
    return { slug, ...(data as Omit<ArticleMeta, "slug">) };
  });
  const filtered = includeDrafts ? all : all.filter((a) => a.status === "published");
  return filtered.sort((a, b) => (a.publishedAt < b.publishedAt ? 1 : -1));
}

export async function getArticleBySlug(slug: string): Promise<Article | null> {
  const fullPath = path.join(CONTENT_DIR, `${slug}.md`);
  if (!fs.existsSync(fullPath)) return null;
  const { data, content } = matter(fs.readFileSync(fullPath, "utf8"));
  const processed = await remark().use(html).process(content);
  return {
    slug,
    contentHtml: processed.toString(),
    ...(data as Omit<ArticleMeta, "slug">),
  };
}
