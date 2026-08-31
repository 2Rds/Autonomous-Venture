// Thin wrapper around GitHub's Contents API, used by the miniapp routes to read/write article
// files directly on `main` — not local fs. Vercel's serverless filesystem is read-only/ephemeral
// and the repo is the actual source of truth this site already deploys from on every push, so
// writing here (rather than to a local checkout) is what makes an approve/edit/reject show up on
// creatorstacked.com without any extra deploy step.

const OWNER = "2Rds";
const REPO = "Autonomous-Venture";
const ARTICLES_PATH = "site/content/articles";
const API_BASE = "https://api.github.com";

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

export interface GithubFile {
  path: string;
  sha: string;
  content: string; // decoded, not base64
}

export async function getArticleFile(
  token: string,
  slug: string
): Promise<GithubFile | null> {
  const path = `${ARTICLES_PATH}/${slug}.md`;
  const resp = await fetch(
    `${API_BASE}/repos/${OWNER}/${REPO}/contents/${path}`,
    { headers: authHeaders(token), cache: "no-store" }
  );
  if (resp.status === 404) return null;
  if (!resp.ok) {
    throw new Error(`GitHub GET ${path} failed: ${resp.status} ${await resp.text()}`);
  }
  const data = await resp.json();
  return {
    path,
    sha: data.sha,
    content: Buffer.from(data.content, "base64").toString("utf8"),
  };
}

export async function listArticleFiles(
  token: string
): Promise<{ slug: string; sha: string }[]> {
  const resp = await fetch(
    `${API_BASE}/repos/${OWNER}/${REPO}/contents/${ARTICLES_PATH}`,
    { headers: authHeaders(token), cache: "no-store" }
  );
  if (resp.status === 404) return [];
  if (!resp.ok) {
    throw new Error(`GitHub GET ${ARTICLES_PATH} failed: ${resp.status} ${await resp.text()}`);
  }
  const entries: { name: string; sha: string; type: string }[] = await resp.json();
  return entries
    .filter((e) => e.type === "file" && e.name.endsWith(".md") && !e.name.startsWith("_"))
    .map((e) => ({ slug: e.name.replace(/\.md$/, ""), sha: e.sha }));
}

export async function putArticleFile(
  token: string,
  slug: string,
  content: string,
  sha: string,
  message: string
): Promise<void> {
  const path = `${ARTICLES_PATH}/${slug}.md`;
  const resp = await fetch(
    `${API_BASE}/repos/${OWNER}/${REPO}/contents/${path}`,
    {
      method: "PUT",
      headers: { ...authHeaders(token), "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        content: Buffer.from(content, "utf8").toString("base64"),
        sha,
        branch: "main",
      }),
    }
  );
  if (!resp.ok) {
    throw new Error(`GitHub PUT ${path} failed: ${resp.status} ${await resp.text()}`);
  }
}

export async function deleteArticleFile(
  token: string,
  slug: string,
  sha: string,
  message: string
): Promise<void> {
  const path = `${ARTICLES_PATH}/${slug}.md`;
  const resp = await fetch(
    `${API_BASE}/repos/${OWNER}/${REPO}/contents/${path}`,
    {
      method: "DELETE",
      headers: { ...authHeaders(token), "Content-Type": "application/json" },
      body: JSON.stringify({ message, sha, branch: "main" }),
    }
  );
  if (!resp.ok) {
    throw new Error(`GitHub DELETE ${path} failed: ${resp.status} ${await resp.text()}`);
  }
}
