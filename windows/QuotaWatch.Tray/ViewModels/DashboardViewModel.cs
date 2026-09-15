using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Media;
using QuotaWatch.Tray.Models;
using QuotaWatch.Tray.Services;
using MediaBrush = System.Windows.Media.Brush;
using MediaBrushes = System.Windows.Media.Brushes;

namespace QuotaWatch.Tray.ViewModels;

public sealed class DashboardViewModel : INotifyPropertyChanged
{
    private string _lastUpdatedText = "Waiting for the first update";
    private string _statusText = "Loading quota data...";
    private bool _isRefreshing;
    private QuotaSeverity _severity = QuotaSeverity.Loading;
    private string _trayTooltip = "Quota Watch — loading";

    public ObservableCollection<ProviderViewModel> Providers { get; } = [];

    public string LastUpdatedText
    {
        get => _lastUpdatedText;
        private set => SetField(ref _lastUpdatedText, value);
    }

    public string StatusText
    {
        get => _statusText;
        private set => SetField(ref _statusText, value);
    }

    public bool IsRefreshing
    {
        get => _isRefreshing;
        private set
        {
            if (SetField(ref _isRefreshing, value))
            {
                OnPropertyChanged(nameof(CanRefresh));
                OnPropertyChanged(nameof(RefreshText));
            }
        }
    }

    public bool CanRefresh => !IsRefreshing;

    public string RefreshText => IsRefreshing ? "Refreshing..." : "Refresh";

    public QuotaSeverity Severity
    {
        get => _severity;
        private set => SetField(ref _severity, value);
    }

    public string TrayTooltip
    {
        get => _trayTooltip;
        private set => SetField(ref _trayTooltip, value);
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public void SetRefreshing(bool refreshing)
    {
        IsRefreshing = refreshing;
        if (refreshing)
        {
            StatusText = "Refreshing quota data...";
        }
    }

    public void ApplySnapshot(QuotaSnapshot snapshot, bool fromCache = false)
    {
        Providers.Clear();
        var generatedAt = snapshot.GeneratedAt == default ? DateTimeOffset.UtcNow : snapshot.GeneratedAt;
        foreach (var provider in snapshot.Providers)
        {
            Providers.Add(ProviderViewModel.FromSnapshot(provider, generatedAt));
        }

        var localTime = generatedAt.ToLocalTime();
        LastUpdatedText = $"Updated {localTime:yyyy-MM-dd HH:mm:ss}";
        StatusText = fromCache ? "Showing the last saved snapshot" : "Updates automatically every 5 minutes";
        RecalculateSummary(snapshot);
    }

    public void SetRefreshError(string message)
    {
        StatusText = $"Refresh failed: {message}";
        Severity = QuotaSeverity.Critical;
        TrayTooltip = "Quota Watch — refresh failed";
    }

    private void RecalculateSummary(QuotaSnapshot snapshot)
    {
        var providers = snapshot.Providers;
        var windowSeverities = Providers
            .SelectMany(provider => provider.Buckets)
            .SelectMany(bucket => bucket.Windows)
            .Select(window => window.Severity)
            .ToList();
        var hasUnavailable = providers.Any(provider => provider.Status.Equals("unavailable", StringComparison.OrdinalIgnoreCase));
        var hasStale = providers.Any(provider => provider.Status.Equals("stale", StringComparison.OrdinalIgnoreCase));
        var hasCriticalWindow = windowSeverities.Contains(QuotaSeverity.Critical);
        var hasWarningWindow = windowSeverities.Contains(QuotaSeverity.Warning);

        Severity = hasUnavailable || hasCriticalWindow
            ? QuotaSeverity.Critical
            : hasStale || hasWarningWindow
                ? QuotaSeverity.Warning
                : QuotaSeverity.Normal;

        var summaries = Providers
            .Select(provider =>
            {
                var providerMax = provider.Buckets.SelectMany(bucket => bucket.Windows).Select(window => window.UsedPercent).DefaultIfEmpty().Max();
                return $"{provider.Name} {providerMax:0.0}%";
            })
            .ToList();
        TrayTooltip = summaries.Count == 0 ? "Quota Watch — no data" : $"Quota Watch — {string.Join(", ", summaries)}";
    }

    private static string DisplayProviderName(string provider)
    {
        return provider.ToLowerInvariant() switch
        {
            "claude" => "Claude",
            "codex" => "Codex",
            _ => string.IsNullOrWhiteSpace(provider) ? "Provider" : provider,
        };
    }

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }

    private void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}

public sealed class ProviderViewModel
{
    public required string Name { get; init; }

    public string PlanText { get; init; } = string.Empty;

    public required string StatusLabel { get; init; }

    public required MediaBrush StatusBrush { get; init; }

    public string Message { get; init; } = string.Empty;

    public bool HasMessage => !string.IsNullOrWhiteSpace(Message);

    public ObservableCollection<BucketViewModel> Buckets { get; init; } = [];

    public static ProviderViewModel FromSnapshot(ProviderSnapshot provider, DateTimeOffset generatedAt)
    {
        var status = provider.Status.ToLowerInvariant();
        var viewModel = new ProviderViewModel
        {
            Name = provider.Provider.ToLowerInvariant() switch
            {
                "claude" => "Claude",
                "codex" => "Codex",
                _ => provider.Provider,
            },
            StatusLabel = status switch
            {
                "available" => "Live",
                "stale" => "Stale",
                "unavailable" => "Unavailable",
                _ => provider.Status,
            },
            StatusBrush = status switch
            {
                "available" => MediaBrushes.LightGreen,
                "stale" => MediaBrushes.Goldenrod,
                "unavailable" => MediaBrushes.IndianRed,
                _ => MediaBrushes.LightSlateGray,
            },
            Message = provider.Message ?? string.Empty,
            PlanText = provider.Buckets.Select(bucket => bucket.PlanType)
                .FirstOrDefault(plan => !string.IsNullOrWhiteSpace(plan))?.ToUpperInvariant() ?? string.Empty,
        };

        foreach (var bucket in provider.Buckets)
        {
            if (provider.Provider.Equals("codex", StringComparison.OrdinalIgnoreCase)
                && (bucket.BucketId.Equals("codex_bengalfox", StringComparison.OrdinalIgnoreCase)
                    || bucket.Name.Equals("GPT-5.3-Codex-Spark", StringComparison.OrdinalIgnoreCase)))
            {
                continue;
            }
            viewModel.Buckets.Add(BucketViewModel.FromSnapshot(bucket, generatedAt));
        }

        return viewModel;
    }
}

public sealed class BucketViewModel
{
    public required string Name { get; init; }

    public string PlanText { get; init; } = string.Empty;

    public ObservableCollection<QuotaWindowViewModel> Windows { get; init; } = [];

    public static BucketViewModel FromSnapshot(QuotaBucket bucket, DateTimeOffset generatedAt)
    {
        var viewModel = new BucketViewModel
        {
            Name = bucket.Name,
            PlanText = string.IsNullOrWhiteSpace(bucket.PlanType) ? string.Empty : bucket.PlanType.ToUpperInvariant(),
        };

        foreach (var window in bucket.Windows)
        {
            viewModel.Windows.Add(QuotaWindowViewModel.FromSnapshot(window, generatedAt));
        }

        return viewModel;
    }
}

public sealed class QuotaWindowViewModel
{
    public required string Label { get; init; }

    public required double UsedPercent { get; init; }

    public required string UsedText { get; init; }

    public required string ResetText { get; init; }

    public required double ElapsedPercent { get; init; }

    public required string TimeText { get; init; }

    public required bool HasElapsedTime { get; init; }

    public required QuotaSeverity Severity { get; init; }

    public required MediaBrush ProgressBrush { get; init; }

    public static QuotaWindowViewModel FromSnapshot(QuotaWindow window, DateTimeOffset generatedAt)
    {
        var used = NormalizeUsedPercent(window.UsedPercent);
        var elapsed = CalculateElapsedPercent(window, generatedAt);
        var severity = CalculateSeverity(used, elapsed);

        return new QuotaWindowViewModel
        {
            Label = window.Label,
            UsedPercent = used,
            UsedText = $"{used:0.0}%",
            ResetText = FormatResetText(window.ResetsAt),
            ElapsedPercent = elapsed ?? 0,
            TimeText = elapsed is double elapsedValue ? $"Time {elapsedValue:0.0}%" : "Time —",
            HasElapsedTime = elapsed.HasValue,
            Severity = severity,
            ProgressBrush = severity switch
            {
                QuotaSeverity.Critical => MediaBrushes.IndianRed,
                QuotaSeverity.Warning => MediaBrushes.Goldenrod,
                _ => MediaBrushes.LightGreen,
            },
        };
    }

    private static double NormalizeUsedPercent(double usedPercent)
    {
        return double.IsFinite(usedPercent) ? Math.Clamp(usedPercent, 0, 100) : 0;
    }

    private static QuotaSeverity CalculateSeverity(double usedPercent, double? elapsedPercent)
    {
        if (usedPercent >= 100)
        {
            return QuotaSeverity.Critical;
        }

        if (elapsedPercent is double elapsed)
        {
            var paceDifference = usedPercent - elapsed;
            if (paceDifference >= 15)
            {
                return QuotaSeverity.Critical;
            }

            return paceDifference > 2 ? QuotaSeverity.Warning : QuotaSeverity.Normal;
        }

        return usedPercent switch
        {
            >= 90 => QuotaSeverity.Critical,
            >= 70 => QuotaSeverity.Warning,
            _ => QuotaSeverity.Normal,
        };
    }

    private static string FormatResetText(long? resetTimestamp)
    {
        if (resetTimestamp is not long timestamp)
        {
            return "Reset time unavailable";
        }

        try
        {
            return $"Resets {DateTimeOffset.FromUnixTimeSeconds(timestamp).ToLocalTime():MM-dd HH:mm}";
        }
        catch (ArgumentOutOfRangeException)
        {
            return "Reset time unavailable";
        }
    }

    private static double? CalculateElapsedPercent(QuotaWindow window, DateTimeOffset generatedAt)
    {
        if (window.ResetsAt is not long resetTimestamp ||
            window.WindowMinutes is not int windowMinutes ||
            windowMinutes <= 0)
        {
            return null;
        }

        DateTimeOffset resetAt;
        try
        {
            resetAt = DateTimeOffset.FromUnixTimeSeconds(resetTimestamp);
        }
        catch (ArgumentOutOfRangeException)
        {
            return null;
        }

        var remainingMinutes = (resetAt - generatedAt).TotalMinutes;
        var elapsedPercent = (windowMinutes - remainingMinutes) / windowMinutes * 100;
        if (!double.IsFinite(elapsedPercent))
        {
            return null;
        }

        return Math.Clamp(elapsedPercent, 0, 100);
    }
}
