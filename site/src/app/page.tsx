import Link from "next/link";
import { getAllArticleMeta } from "@/lib/content";

export default function Home() {
  const articles = getAllArticleMeta();

  return (
    <main className="max-w-3xl mx-auto px-6 py-16">
      <h1 className="text-3xl font-semibold mb-2">[Site name TBD]</h1>
      <p className="text-neutral-600 mb-10">
        Practical guides for course creators and coaches choosing the tools to run their business.
      </p>

      {articles.length === 0 ? (
        <p className="text-neutral-500">
          No published articles yet — content pipeline is wired but empty. See{" "}
          <code>content/articles/</code>.
        </p>
      ) : (
        <ul className="space-y-6">
          {articles.map((article) => (
            <li key={article.slug}>
              <Link href={`/articles/${article.slug}`} className="text-lg font-medium hover:underline">
                {article.title}
              </Link>
              <p className="text-neutral-500 text-sm">{article.description}</p>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
