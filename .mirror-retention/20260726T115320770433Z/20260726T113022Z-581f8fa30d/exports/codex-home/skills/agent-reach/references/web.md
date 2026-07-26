# Web Source Selection

Use for deciding how to acquire ordinary webpages, articles, websites, or RSS content.

## Known URL

Do not perform source discovery. Submit the URL directly to the generic webpage acquisition route with the desired output:

```yaml
intent: explicit_user_url
target: known URL
source_classes: [web]
need_materialization: false
output: content
```

The resource layer may select Firecrawl, MarkItDown, Playwright, Chrome/DevTools, or another configured owner according to page type and access needs.

## Site Or Article Discovery

Use a discovery request with keywords, domains, language, freshness, and result count. Return candidates before crawling a whole site when scope is uncertain.

## RSS

Request feed discovery or parsing explicitly. Preserve item title, canonical link, publish date, author, and feed identity. Do not treat a feed description as the full article.

## Rules

- Do not default to curl, Jina Reader, mcporter, or a named scraper from this reference.
- Use an authenticated browser owner for pages that genuinely require the user's live session.
- Respect robots, access, permission, and paywall boundaries.
- Distinguish fetched content from snippets or search metadata.
- For documentation sites, route named library/API docs to the documentation owner instead of generic crawling when appropriate.

Return the selected owner class, acquisition scope, canonical URL, content completeness, and blockers.
