# Claude active refresh

Quota Watch refreshes Claude subscription usage independently of the interactive
Claude Code status line on Windows systems whose Claude login lives in WSL.

## Data flow

1. The Windows `quota` process starts a short-lived Python helper in the default
   WSL distribution, or in `QUOTA_WATCH_CLAUDE_WSL_DISTRO` when configured.
2. The helper reads `~/.claude/.credentials.json` inside WSL and sends the OAuth
   access token only to `https://api.anthropic.com/api/oauth/usage`.
3. The helper filters the response to the 5-hour and 7-day utilization, reset
   timestamps, subscription type, and model-scoped weekly limits (such as Fable)
   before writing JSON to stdout. Model names come from the usage response.
4. Windows stores only that filtered snapshot. OAuth access and refresh tokens,
   account data, and organization data never cross the WSL boundary and are not
   written to Quota Watch files or logs.

## Refresh and fallback behavior

- Automatic OAuth attempts are limited to one every 10 minutes across CLI and
  tray refreshes.
- A failed request never overwrites the last valid quota snapshot.
- The existing Claude status-line bridge remains available as a fallback and may
  update the same filtered cache between active refreshes.
- Cached windows whose reset timestamp has passed are hidden instead of being
  presented as current usage.
- Quota Watch does not refresh, rotate, or write Claude credentials.
- All models and model-scoped limits are cached separately. Status-line updates
  retain unexpired model buckets; missing model percentages are never shown as zero.
- The subscription tier appears once beside the provider heading.

## Compatibility boundary

The OAuth usage endpoint is used by Claude Code's own usage view but is not a
documented public Anthropic API. It may change without notice. The status-line
fallback and failure-safe cache behavior are therefore required parts of this
feature.

## Acceptance checks

- Successful responses populate the 5-hour and 7-day windows.
- Repeated collection within 10 minutes performs no second OAuth request.
- HTTP, authentication, timeout, malformed-response, and missing-credential
  failures retain the last valid snapshot.
- Tests and logs contain no credential values.
