import { notFound } from "next/navigation";
import { getAllArticleMeta, getArticleBySlug } from "@/lib/content";
import { DisclosureBanner } from "@/components/DisclosureBanner";

export async function generateStaticParams() {
  return getAllArticleMeta(true).map((article) => ({ slug: article.slug }));
}

export default async function ArticlePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const article = await getArticleBySlug(slug);
  if (!article) notFound();

  return (
    <main className="max-w-2xl mx-auto px-6 py-16">
      {article.status === "draft" && (
        <p className="text-sm bg-amber-100 text-amber-900 rounded px-3 py-2 mb-6">
          Draft — not published, only reachable directly by slug.
        </p>
      )}
      <h1 className="text-3xl font-semibold mb-4">{article.title}</h1>
      <article
        className="prose prose-neutral max-w-none"
        dangerouslySetInnerHTML={{ __html: article.contentHtml }}
      />
      <DisclosureBanner />
    </main>
  );
}
