# Security

Quota Watch is designed to avoid storing provider credentials.

- The Codex adapter launches the locally installed `codex app-server` and calls its read-only rate-limit method.
- The Claude bridge stores only quota percentages, reset timestamps, and the capture time.
- Claude account data, session IDs, working directories, cookies, and OAuth tokens are discarded.
- Existing Claude Code status-line configuration is never overwritten automatically.

Please report security issues privately through GitHub's security advisory flow instead of opening a public issue.
