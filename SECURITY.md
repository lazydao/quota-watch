# Security

Quota Watch is designed to avoid storing provider credentials.

- The Codex adapter launches the locally installed `codex app-server` and calls its read-only rate-limit method.
- On Windows, the Claude active-refresh helper reads Claude Code's OAuth access token inside WSL and sends it only to `https://api.anthropic.com/api/oauth/usage`. The token never appears in Windows process arguments, stdout, logs, or Quota Watch cache files.
- Quota Watch does not refresh, rotate, or write Claude credentials. Active-refresh failures fall back to filtered local quota snapshots, including status-line cache and Windows Claude Desktop history.
- The Claude bridge stores only quota percentages, reset timestamps, and the capture time.
- On Windows, the Claude Desktop fallback reads only `plan-usage-history.json` from the regular app or current Microsoft Store data directory. It returns filtered 5-hour and 7-day percentages and their original capture time, discards organization and unrelated fields, and does not overwrite the CLI cache.
- The desktop fallback never reads cookies, session keys, or authentication files, never calls a network endpoint, and never changes Claude Desktop settings. Missing reset times, model quotas and subscription tiers are not inferred from other sources.
- Claude account data, session IDs, working directories, cookies, and OAuth tokens are discarded.
- Existing Claude Code status-line configuration is never overwritten automatically.

Please report security issues privately through GitHub's security advisory flow instead of opening a public issue.
