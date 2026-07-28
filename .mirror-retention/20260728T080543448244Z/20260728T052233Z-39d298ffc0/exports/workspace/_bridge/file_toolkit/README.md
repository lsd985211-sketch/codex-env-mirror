# File Toolkit

Read-only file analysis helpers for mobile bridge attachments.

## Scope

- Detect and summarize local files sent through the OpenClaw mobile bridge.
- Keep attachment analysis separate from bridge queue and Codex delivery logic.
- Never edit source attachments from this toolkit.
- Resource acquisition, copying, hashing, download, and cache metadata belong
  in `_bridge/resource_fetcher.py`. This toolkit should only inspect files that
  already exist locally.

## Resource Boundary

Use `_bridge/resource_fetcher.py` before analysis when a workflow needs to
download, copy, validate, or cache a file. The resource layer returns a stable
local path, sha256, size, cache-hit flag, and error reason; then this toolkit can
preview or analyze that local path.

Use `_bridge/resource_router.py` or `python _bridge\resource_cli.py route
--json` to decide the route before materializing ambiguous resources. The
router is read-only: it does not fetch, call MCP tools, open browsers, install
packages, or write files.

Use purpose-built MCP tools before materializing a resource when the task only
needs structured information, documentation, conversion, or page evidence:

- `context7`: current library, framework, SDK, CLI, API, and cloud-service docs.
- `microsoftdocs`: Microsoft Learn documentation.
- `github`: GitHub repository, issue, PR, action, and release metadata.
- `markitdown`: conversion from file/http/data resources to Markdown when a
  durable cache entry is not needed.
- `playwright` and `chrome-devtools`: browser/page inspection, screenshots,
  console/network evidence, and E2E-style checks.

Boundary rule: MCPs answer, inspect, or convert; `_bridge/resource_cli.py`
materializes, verifies, caches, and logs. If a resource must become a stable
local artifact with sha256/size metadata or later replay, route it through the
resource layer even if an MCP was used for discovery first.

For command-line workflows, use `_bridge/resource_cli.py` instead of ad hoc
`curl`, `Invoke-WebRequest`, or one-off copy scripts:

```powershell
python _bridge\resource_cli.py acquire --intent explicit_user_url --stage probe --url "https://example.test/file.zip" --json
python _bridge\resource_cli.py acquire --intent explicit_user_url --stage preview --url "https://example.test/file.zip" --json
python _bridge\resource_cli.py acquire --intent explicit_user_url --stage materialize --url "https://example.test/file.zip" --target-dir _bridge\resources --json
python _bridge\resource_cli.py acquire --intent explicit_local_file --path ".\local-file.txt" --target-dir _bridge\resources --json
python _bridge\resource_cli.py route --url "https://docs.python.org/3/library/json.html" --task "look up docs" --json
python _bridge\resource_cli.py verify "_bridge\resources\file.txt" --sha256 <digest> --json
python _bridge\resource_cli.py inspect-cache --target-dir _bridge\resources
python _bridge\resource_cli.py clean-cache --target-dir _bridge\resources --older-than-days 30 --dry-run
```

Download policy notes:

- Prefer `acquire --intent ... --stage ...` for new workflows. `fetch-url` and
  `fetch-file` remain compatibility shortcuts and are logged as legacy commands
  with explicit intent, stage, and policy metadata.
- Only explicit user URLs should materialize by default. Inline URLs,
  dependency URLs, and documentation URLs should stay at discover/probe/preview
  or deferred until the task semantics are clear.
- URL materialization uses the policy allow-list for schemes. Use explicit
  review for unusual schemes instead of bypassing the resource layer.
- `--max-bytes` is optional. Use it when a workflow needs a protective cap; it
  is not required for large files.
- `fetch-url` retries transient download failures by default. Use `--retries`
  and `--retry-delay` to tune this per task, or use `acquire` when intent/stage
  needs to be visible at the call site.
- When `--sha256` is provided, mismatches fail and do not enter the cache.

## Supported Preview Types

- Text and config: `txt`, `md`, `log`, `json`, `xml`, `yaml`, `toml`, `ini`, `cfg`, `properties`
- Tables: `csv`, `xlsx`, `xlsm`, optional `xls`
- Documents: `docx`, optional OpenDocument `odt`
- Presentations: optional `pptx`
- PDF: `pdf`
- Images: common raster formats, with metadata when Pillow is available
- Archives: `zip`, `jar`, `mcpack`, `mcaddon`, optional `7z`

## Dependency Install

```powershell
cd C:\Users\45543\Downloads\mcsmanager_windows_release\mcsmanager
.\_bridge\file_toolkit\install-deps.ps1
```

System-level tools such as Tesseract OCR, ExifTool, LibreOffice, and 7-Zip are
not installed by this script. Add those only when a real workflow needs them.
