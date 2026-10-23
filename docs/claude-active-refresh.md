# Claude active refresh

Quota Watch refreshes Claude subscription usage independently of the interactive
Claude Code status line, preferring the existing Windows Claude Code login.

## Data flow

1. The Windows `quota` process first reads `CLAUDE_CODE_OAUTH_TOKEN`, when
   explicitly set, or `.credentials.json` under `CLAUDE_CONFIG_DIR` (default
   `%USERPROFILE%\.claude`). It never reads a token from Claude Desktop cookies.
2. It makes a GET to `https://api.anthropic.com/api/oauth/usage`, refusing
   redirects. Known-expired file tokens and known-missing `user:profile` scope
   fail locally. Environment tokens must have quota-query permission too; the
   variable does not grant extra scope or make a token long-lived.
3. On failure, the fresh CLI/status-line cache is preferred, then fresh Windows
   Claude Desktop history. Only if neither is usable and fresh does the process
   start the existing short-lived WSL helper in the default distribution, or in
   `QUOTA_WATCH_CLAUDE_WSL_DISTRO` when configured.
4. The WSL helper reads `~/.claude/.credentials.json` inside WSL. Tokens stay in
   that environment; only filtered usage fields return to Windows.
5. Both active paths filter to quota utilization, reset times, subscription type
   when known, and model-scoped weekly limits (such as Fable). Windows stores
   only that snapshot, not tokens, account details, or organization data.

## Refresh and fallback behavior

- Automatic collection attempts, including their fallback chain, are limited to
  one every 10 minutes across CLI and tray refreshes. The Windows and WSL paths
  share a timeout budget rather than adding two full query timeouts.
- A failed request never overwrites the last valid quota snapshot.
- The existing Claude status-line bridge remains available as a fallback and may
  update the same filtered cache between active refreshes.
- Cached windows whose reset timestamp has passed are hidden instead of being
  presented as current usage.
- Successful CLI/status-line snapshots remember reset times in the existing
  quota cache, separately from percentages. A missing reset can reuse the last
  still-future value for the same bucket, window label and duration, including
  when an intervening snapshot omitted that window. A newly supplied reset
  takes precedence. Expired values are not reused or advanced to another cycle.
- Quota Watch does not refresh, rotate, or write Claude credentials, launch
  login, or install a CLI. The official Claude Code login must be valid for
  unattended active querying. No model call is used for querying or keepalive.
- All models and model-scoped limits are cached separately. Status-line updates
  retain unexpired model buckets; missing model percentages are never shown as zero.
- The subscription tier appears once beside the provider heading.
- When the Windows query fails or collection is cooling down, recent CLI cache data stays
  preferred. If that cache is stale or has no usable windows, Windows can read
  Claude Desktop's local `plan-usage-history.json` as a partial fallback. Of the
  usable stale CLI cache and desktop history, the newer capture is selected.
- Desktop samples retain their original capture time; samples older than
  15 minutes are marked stale. The desktop fallback never overwrites the CLI
  cache or changes the 10-minute OAuth request limit.
- Desktop history supplies only all-model 5-hour and 7-day percentages. Missing
  reset times may use the remembered values described above; `Time —` is shown
  when no unexpired match exists. The message identifies remembered resets.
  Percentages and capture times remain from Desktop, and no old model quotas
  or subscription tiers are added. This single-account cache assumes all
  configured Claude sources use the same account.
- The regular Windows installation, legacy Store cache, and relocated Store
  history under `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc` are detected.
  Reading the history does not access cookies or credentials,
  contact a provider endpoint, or change Claude Desktop's settings.

## Compatibility boundary

The OAuth usage endpoint is used by Claude Code's own usage view but is not a
documented public Anthropic API. It may change without notice. The status-line
fallback and failure-safe cache behavior are therefore required parts of this
feature.

The desktop history is also a private, versioned file format, not a supported
public API. Only its observed version-2 quota samples are accepted; missing,
unreadable, malformed or unsupported histories are ignored. Claude Desktop may
pause its own background updates while idle, so leaving the app open does not
guarantee fresh fallback data.

## Acceptance checks

- Successful responses populate the 5-hour and 7-day windows.
- Repeated collection within 10 minutes performs no second OAuth request.
- HTTP, authentication, timeout, malformed-response, and missing-credential
  failures retain the last valid snapshot.
- Tests and logs contain no credential values.
- Desktop fallback preserves capture time, ignores unrelated fields, never
  invents missing windows, and reuses only matching, unexpired reset times.
- Reset memory survives missing fields/windows, prefers newly supplied resets,
  ignores malformed metadata, and does not change the public JSON schema.
- Successful active queries recover automatically to the complete data source.
- Windows success or fresh local fallback does not launch WSL, including an
  auth-status subprocess just to fill an unknown subscription label.
- Missing or broken native credentials, HTTP 401/403, redirects, and malformed
  responses fail safely without credential writes or secret-bearing errors.

See [Windows-first collection](windows-first-collection.md) for the full source
order, Codex App discovery, and acceptance criteria.
