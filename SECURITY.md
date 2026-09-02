# Security

Quota Watch is designed to avoid storing provider credentials.

- The Codex adapter launches the locally installed `codex app-server` and calls its read-only rate-limit method.
- On Windows, the Claude active-refresh helper reads Claude Code's OAuth access token inside WSL and sends it only to `https://api.anthropic.com/api/oauth/usage`. The token never appears in Windows process arguments, stdout, logs, or Quota Watch cache files.
- Quota Watch does not refresh, rotate, or write Claude credentials. Active-refresh failures fall back to the filtered status-line cache.
- The Claude bridge stores only quota percentages, reset timestamps, and the capture time.
- Claude account data, session IDs, working directories, cookies, and OAuth tokens are discarded.
- Existing Claude Code status-line configuration is never overwritten automatically.

Please report security issues privately through GitHub's security advisory flow instead of opening a public issue.
