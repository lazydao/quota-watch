# Security

Quota Watch is designed to avoid storing provider credentials.

- The Codex adapter prefers the Windows Codex App backend, then the standalone CLI. It launches `codex app-server` and calls only initialization and the read-only rate-limit method. It does not start threads, send model prompts, read token files itself, or override the provider's existing credential-store configuration.
- Claude active refresh first reads the existing Windows Claude Code access token from `CLAUDE_CODE_OAUTH_TOKEN` or `.credentials.json` under `CLAUDE_CONFIG_DIR` (default `%USERPROFILE%\.claude`). It sends a GET to the fixed `https://api.anthropic.com/api/oauth/usage` endpoint, refuses redirects, and returns filtered quota fields. Known-expired file tokens and known-missing profile scope fail before a network call. Raw network error text and response bodies are not logged.
- WSL is used only when Windows has no fresh usable data. That helper reads Claude Code's OAuth access token inside WSL and returns only filtered quota data. WSL tokens never cross into the Windows process.
- Neither path puts access tokens in process arguments, stdout, logs, or Quota Watch cache files. Collection makes no inference request and consumes no model tokens.
- Quota Watch does not refresh, rotate, or write Claude credentials. Active-refresh failures fall back to filtered local quota snapshots, including status-line cache and Windows Claude Desktop history.
- The Claude bridge stores only quota percentages, reset timestamps, and the capture time.
- On Windows, the Claude Desktop fallback reads only `plan-usage-history.json` from the regular app or current Microsoft Store data directory. It returns filtered 5-hour and 7-day percentages and their original capture time, discards organization and unrelated fields, and does not overwrite the CLI cache.
- The desktop fallback never reads cookies, session keys, or authentication files, never calls a network endpoint, and never changes Claude Desktop settings. Only a matching, still-future reset time may be reused from the existing single-account quota cache; percentages and capture times remain from Desktop, and model quotas and subscription tiers are not copied from other sources. Reset memory contains quota window identifiers and timestamps only, and never guesses another cycle.
- Claude account data, session IDs, working directories, cookies, and OAuth tokens are discarded.
- Existing Claude Code status-line configuration is never overwritten automatically.

Please report security issues privately through GitHub's security advisory flow instead of opening a public issue.
