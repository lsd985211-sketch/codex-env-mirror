---
name: github-ops
description: GitHub repository operations for Codex on this machine. Use when working with GitHub remotes, repositories, releases, tags, issues, pull requests, Actions, remote files, GitHub CLI, GitHub MCP, Hub github.api/github.gh, Secret Vault GitHub tokens, GitHub App installation tokens, or when diagnosing GitHub authentication and permission routing.
---

# GitHub Ops

Use this skill for GitHub remote state and remote writes. Local `git` proves only
local repository state; use GitHub MCP, Hub, `gh`, or REST readback to prove
remote state.

## Role Boundaries

- Own GitHub remote reads/writes, GitHub authentication routing, and remote readback evidence.
- Do not own local repository history or diffs, generic web research, or secret materialization; hand those to local git, the resource layer, or Secret Vault owners.

## Route Selection

1. GitHub is `hub_first`; use the known Hub route before any native probe:
   - `github.api` for REST endpoints and structured responses.
   - `github.gh` for high-level `gh` workflows.
2. If Hub is unavailable or insufficient, continue forward through Hub gateway/local Hub, then direct `gh` CLI with the same credential boundary. Do not jump backward to native after entering at the Hub stage.
3. Use local `git` only for local history, diffs, branches, commits, and SSH/HTTPS
   transport checks.

Do not treat an old failure as current evidence. Native GitHub MCP callability
remains separate health evidence, but a Hub-first operation does not probe or
fall back backward to native merely to refresh it.

## Credential Boundaries

- `gh` OAuth/keyring: best for local CLI, Git HTTPS credential helper, releases,
  issues, PRs, and ordinary interactive workflows.
- Secret Vault `github.token`: automation credential source for Hub REST fallback
  and for native GitHub MCP bearer-token injection at Codex startup. Never print
  it.
- GitHub App aliases: `github_app.app_id`, `github_app.installation_id`,
  `github_app.private_key`. Use them only through `github_app_auth.py` or Hub;
  generated JWTs and installation tokens are short-lived handoffs, not stored
  secrets.
- Environment tokens may exist but can be stale. If GitHub returns `401 Bad
  credentials`, try the next configured source. Do not use a different
  credential to bypass a `403` permission denial.

Never pass tokens in command-line arguments, write them to ordinary files, or
quote them in replies. Use Secret Vault stdin storage, `gh` keyring, or module
handoff.

## Native GitHub MCP Auth Repair

When native GitHub MCP returns `Auth required`, treat it as a credential injection
or current-process environment problem before assuming the MCP server is broken.

Root-cause sequence:

1. Check the configured token environment variable:
   - `Select-String -Path C:\Users\45543\.codex\config.toml -Pattern "mcp_servers.github|bearer_token_env_var|githubcopilot"`
2. Check Secret Vault without printing token material:
   - `python _bridge\secret_vault.py snapshot`
   - `python _bridge\secret_vault.py doctor`
   - `python _bridge\secret_vault.py get --alias github.token` should return
     `secret_printing_blocked`.
3. Check an independent GitHub credential path:
   - `gh auth status`
   - `gh api user --jq "{login,id}"`
4. Check the launcher injection path:
   - `Select-String -Path C:\Users\45543\.codex\scripts\start-codex-desktop-elevated.ps1 -Pattern "Set-GitHubMcpBearerTokenFromVault|GITHUB_PERSONAL_ACCESS_TOKEN|github.token"`
   - PowerShell parse check for that startup script.

The durable local fix is launcher-level, not config-level: Codex startup should
read `github.token` from Secret Vault and set only the Codex process environment
variable expected by `bearer_token_env_var`. Do not write the token into
`config.toml`, user/system environment variables, logs, ordinary files, or chat.

Important boundary: a running Codex Desktop process cannot receive a newly
injected environment variable retroactively. If the launcher was repaired during
the current session, native GitHub MCP may keep returning `Auth required` until
Codex Desktop is restarted through the controlled launcher. During that same
    turn, continue with Hub or `gh` using the same permission boundary, and mark native
MCP verification as pending restart rather than repeatedly retrying the same
failing native call.

After restart, verify native MCP with `github/get_me`. If it succeeds, record a
current-turn callable observation through the MCP session doctor. If it still
fails, compare `config_ok`, `protocol_ok`, `current_turn_exposed`, and
`current_turn_callable` before changing credentials again.

## Write Rules

For remote writes, confirm the intended target and operation first unless the
user already authorized that exact write. Hub write calls require:

`write_ack=github-write-through-hub-uses-existing-permissions`

### Repository Visibility Default

- Newly created repositories default to `public` when the user asks to create
  or publish a repository and does not specify visibility.
- This is a creation default, not permission to change an existing repository.
  Read the current visibility before editing an existing repository.
- Before making a new repository public, run the repository publication safety
  check: no credentials, tokens, cookies, sessions, raw databases, private
  archives, unapproved machine state, or restricted material may be included.
- If the safety check fails or the content is not clearly publishable, stop and
  request an explicit visibility decision. Do not silently fall back to private
  or public.

Treat these as write-capable:

- REST `POST`, `PATCH`, `PUT`, `DELETE`.
- `gh repo`, `gh issue`, `gh pr`, `gh release`, `gh workflow`, `gh run`,
  `gh secret`, `gh variable`, `gh api` with mutating methods or body flags.

Do not broaden permissions by switching credential sources. The active token or
GitHub App installation permissions define the boundary.

## Common Workflows

### Auth And Capability Check

- `gh auth status`
- `gh api user --jq ".login"`
- `python _bridge\secret_vault.py snapshot`
- `python _bridge\secret_vault.py doctor`
- `python _bridge\github_app_auth.py snapshot`
- `python _bridge\github_app_auth.py validate --online` when App aliases exist.
- `python _bridge\local_mcp_hub.py validate`

### Release Work

1. Verify local tag/commit with `git` if working from a local repo.
2. Create or update release with GitHub MCP, Hub `github.gh`, or `gh release`.
3. Read back the remote release by tag:
   - `gh release view <tag> --repo <owner>/<repo> --json tagName,url,targetCommitish`
   - or Hub `github.api` GET `/repos/<owner>/<repo>/releases/tags/<tag>`.

### Issue And PR Work

1. Read current state before mutating.
2. For reviews, prefer Hub `github.api`/`github.gh`; use native review tools when Hub cannot represent the required review operation.
3. For every route, keep comments concise and verify URL/status after write.
4. Do not infer merge, close, label, or assignment success from local branch
   state; read back the remote issue/PR.

### Remote File Edits

1. Read the file and current SHA remotely.
2. Submit a minimal update using the current SHA.
3. Read back the file metadata or commit SHA.
4. Avoid rewriting generated files or secrets.

## Validation

Use the smallest proof that matches the action:

- Auth: account login and credential source, with no token value.
- Read: returned HTTP status, URL, ID, tag, issue/PR number, or SHA.
- Write: returned URL/SHA plus a fresh readback.
- Release: tag name, target commitish, draft/prerelease flags, published URL.
- Credential repair: `gh auth status`, Secret Vault doctor, GitHub App doctor,
  one harmless API read, and native GitHub MCP `get_me` after any required Codex
  restart.

## External References

Use current official docs when behavior is uncertain:

- GitHub CLI manual: `https://cli.github.com/manual/`
- GitHub REST authentication: `https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api`
- GitHub App JWT auth: `https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app`
- GitHub App installation tokens: `https://docs.github.com/en/rest/apps/apps#create-an-installation-access-token-for-an-app`

## Preflight

- Confirm whether the task is local Git state, remote GitHub state, or both.
- Identify the owning credential route before remote writes.
- Keep token values out of prompts, logs, files, and replies.

## Output Contract

- State the route used, credential source label, operation result, and validation
  evidence.
- If GitHub App is installed but App aliases are missing, say that installation
  alone is insufficient and name the missing aliases.
