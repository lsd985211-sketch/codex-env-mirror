# Social Source Selection

Use for public discussions, posts, comments, or community signals from Xiaohongshu, X, Bilibili, V2EX, Reddit, and similar platforms.

## Platform Fit

| Need | Source class |
|---|---|
| Chinese consumer experience | Xiaohongshu, Bilibili, V2EX |
| Developer and technical discussion | Reddit, V2EX, X |
| Creator/video community context | Bilibili, YouTube, X |
| Public reactions to a current event | two distinct social sources plus an authoritative source |

## Request Fields

```yaml
intent: social_discovery
query: exact topic or account
source_classes: [social]
preferred_domains: []
language: any
region: any
freshness: recent
result_count: 20
output: candidates
constraints:
  platform: optional
  public_only: true
```

The resource layer selects OpenCLI, an owner API, an authenticated browser surface, a search adapter, or another configured backend. Do not encode one platform CLI as the universal default.

## Access Rules

- Collect public or user-authorized content only.
- Report login, rate-limit, deleted-content, or access blockers.
- Do not extract cookies, bypass captchas, conceal automation, or upgrade/install platform tools without the resource approval path.
- Treat social evidence as experience or opinion unless independently verified.

## Refinement

- Too much noise: add platform, account, date range, language, or exact phrase.
- Too little coverage: add a second platform with a distinct audience.
- Login required: request the appropriate authenticated owner surface.
- Backend failure: continue the configured owner/resource route; do not silently switch to direct generic web.

Return platform, author/account, publish time, canonical URL, short excerpt/summary, and access limitations.
