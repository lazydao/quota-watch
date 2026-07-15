using System.ComponentModel;
using System.Diagnostics;
using QuotaWatch.Tray.Models;

namespace QuotaWatch.Tray.Services;

public sealed class QuotaCliClient
{
    private readonly TimeSpan _timeout;

    public QuotaCliClient(TimeSpan? timeout = null)
    {
        _timeout = timeout ?? TimeSpan.FromSeconds(20);
    }

    public async Task<QuotaSnapshot> FetchAsync(CancellationToken cancellationToken = default)
    {
        var executable = Environment.GetEnvironmentVariable("QUOTA_WATCH_CLI_PATH") ?? "quota";
        var startInfo = new ProcessStartInfo
        {
            FileName = executable,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        startInfo.ArgumentList.Add("--json");
        startInfo.ArgumentList.Add("--codex-timeout");
        startInfo.ArgumentList.Add("8");

        using var process = new Process { StartInfo = startInfo };
        try
        {
            if (!process.Start())
            {
                throw new InvalidOperationException("Unable to start the quota CLI.");
            }
        }
        catch (Win32Exception error)
        {
            throw new InvalidOperationException(
                "The quota command was not found. Install quota-watch or set QUOTA_WATCH_CLI_PATH.",
                error);
        }

        var outputTask = process.StandardOutput.ReadToEndAsync(cancellationToken);
        var errorTask = process.StandardError.ReadToEndAsync(cancellationToken);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        timeout.CancelAfter(_timeout);

        try
        {
            await process.WaitForExitAsync(timeout.Token);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            TryKill(process);
            throw new TimeoutException("The quota CLI did not respond in time.");
        }

        var output = await outputTask;
        var errorOutput = await errorTask;
        if (process.ExitCode != 0)
        {
            var detail = string.IsNullOrWhiteSpace(errorOutput) ? $"exit code {process.ExitCode}" : errorOutput.Trim();
            throw new InvalidOperationException($"The quota CLI failed: {detail}");
        }

        return QuotaSnapshotParser.Parse(output);
    }

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited)
            {
                process.Kill(entireProcessTree: true);
            }
        }
        catch (InvalidOperationException)
        {
        }
    }
}
