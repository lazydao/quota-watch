# Windows-first quota collection

## Goal

Prefer the user's existing Windows provider installation and actively read
subscription quotas without creating a conversation, sending a prompt, or
consuming model tokens. Keep the flyout layout and JSON schema unchanged.

## Selection order

### Claude

1. When the shared 10-minute query interval is due, query the usage endpoint
   with the Windows Claude Code credential. Respect `CLAUDE_CONFIG_DIR` and an
   explicitly supplied `CLAUDE_CODE_OAUTH_TOKEN`; the environment token does not
   gain any additional scopes or lifetime from being supplied this way.
2. If that query is unavailable, prefer a fresh local snapshot: the existing
   filtered CLI/status-line cache, then Windows Claude Desktop history.
3. Only when Windows has no fresh usable data, try the existing WSL query.
4. If all active paths fail, retain the newest usable local snapshot and its
   original capture time, including the existing stale/partial-data behavior.

The 10-minute interval limits collection attempts, including their fallback
chain, across CLI and tray runs. It does not turn cached data into a fresh
server response. A successful Windows query never starts WSL. A missing reset
time may reuse the last still-future reset cached for the exact bucket, window
label and duration; it is never advanced into a new cycle. Percentages and
capture times stay with their selected source. Missing plan information or
model windows are not filled from another source. The single-account cache
assumes all configured Claude sources use the same account.

### Codex

Keep `QUOTA_WATCH_CODEX_PATH` as the explicit override. Otherwise prefer the
newest installed Windows Codex App backend, then its legacy executable, then
the standalone CLI and PATH. Use only app-server initialization and
`account/rateLimits/read`; leave authentication to the official backend.
Respect the caller's existing `CODEX_HOME` and credential-store settings.

## Security and compatibility

- Claude's native request is a GET to the fixed HTTPS usage endpoint. Do not
  follow redirects with a bearer token, call inference, refresh/rotate tokens,
  launch login, or write provider credentials.
- Known-expired file tokens and known-missing profile scope fail locally.
  HTTP 401/403, malformed/missing files, and network errors fail safely without
  exposing token values or raw server error bodies.
- Do not read Claude Desktop cookies or extract tokens from its processes.
- Desktop history and the Claude usage endpoint remain private interfaces.
  An open desktop app does not guarantee current history or a valid CLI login.
- Never silently install or update either provider's software.

## Acceptance and verification

- Tests cover Windows success without WSL, fresh local fallback before WSL,
  WSL fallback, query cooldown, and retention of stale data on total failure.
- Exercise native Claude requests with synthetic credentials, including
  config-directory overrides, token errors, redirection, and secret filtering.
- Test Codex executable precedence, missing installs, and the exact read-only
  JSON-RPC method sequence.
- Run the Python suite, Windows build, and tray smoke tests. Run the real CLI
  against local data without a model request and report authentication limits.
