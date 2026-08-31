/**
 * Every outbound affiliate link must render through this component, never as a raw <a>.
 * `rel` covers both the FTC-disclosure expectation (readers should be able to tell it's
 * a paid link from context, reinforced by the page-level disclosure banner) and the
 * technical requirement most affiliate networks impose (nofollow/sponsored on paid links).
 */
export function AffiliateLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <a href={href} target="_blank" rel="nofollow sponsored noopener noreferrer">
      {children}
    </a>
  );
}
