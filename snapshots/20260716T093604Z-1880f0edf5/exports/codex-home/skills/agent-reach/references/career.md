# Career Source Selection

Use for job, employer, role, and professional-profile discovery.

## Request Fields

```yaml
intent: job_search
query: role and decision need
keywords: [role, skills]
region: explicit location or remote
freshness: current
source_classes: [career, official, community]
preferred_domains: []
result_count: 20
output: candidates
```

Prefer official employer career pages and structured job sources for current openings. Use professional-network evidence for public profile context only when access is authorized. Treat salary aggregators and community discussions as secondary evidence.

Do not call LinkedIn scrapers, Jina, curl, or login-dependent commands directly by default. Let the resource layer select an available owner and report authentication or access blockers.

Return role, employer, location, publish date, canonical URL, source type, and any missing evidence. Do not infer that a stale or mirrored listing is still open.
