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
        foreach (var provider in snapshot.Providers)
        {
            Providers.Add(ProviderViewModel.FromSnapshot(provider));
        }

        var localTime = snapshot.GeneratedAt.ToLocalTime();
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
        var allWindows = providers.SelectMany(provider => provider.Buckets).SelectMany(bucket => bucket.Windows).ToList();
        var maxUsed = allWindows.Count == 0 ? 0 : allWindows.Max(window => window.UsedPercent);
        var hasUnavailable = providers.Any(provider => provider.Status.Equals("unavailable", StringComparison.OrdinalIgnoreCase));
        var hasStale = providers.Any(provider => provider.Status.Equals("stale", StringComparison.OrdinalIgnoreCase));

        Severity = hasUnavailable || maxUsed >= 90
            ? QuotaSeverity.Critical
            : hasStale || maxUsed >= 70
                ? QuotaSeverity.Warning
                : QuotaSeverity.Normal;

        var summaries = providers
            .Select(provider =>
            {
                var providerMax = provider.Buckets.SelectMany(bucket => bucket.Windows).Select(window => window.UsedPercent).DefaultIfEmpty().Max();
                return $"{DisplayProviderName(provider.Provider)} {providerMax:0.0}%";
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

    public required string StatusLabel { get; init; }

    public required MediaBrush StatusBrush { get; init; }

    public string Message { get; init; } = string.Empty;

    public bool HasMessage => !string.IsNullOrWhiteSpace(Message);

    public ObservableCollection<BucketViewModel> Buckets { get; init; } = [];

    public static ProviderViewModel FromSnapshot(ProviderSnapshot provider)
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
        };

        foreach (var bucket in provider.Buckets)
        {
            viewModel.Buckets.Add(BucketViewModel.FromSnapshot(bucket));
        }

        return viewModel;
    }
}

public sealed class BucketViewModel
{
    public required string Name { get; init; }

    public string PlanText { get; init; } = string.Empty;

    public ObservableCollection<QuotaWindowViewModel> Windows { get; init; } = [];

    public static BucketViewModel FromSnapshot(QuotaBucket bucket)
    {
        var viewModel = new BucketViewModel
        {
            Name = bucket.Name,
            PlanText = string.IsNullOrWhiteSpace(bucket.PlanType) ? string.Empty : bucket.PlanType.ToUpperInvariant(),
        };

        foreach (var window in bucket.Windows)
        {
            viewModel.Windows.Add(QuotaWindowViewModel.FromSnapshot(window));
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

    public required MediaBrush ProgressBrush { get; init; }

    public static QuotaWindowViewModel FromSnapshot(QuotaWindow window)
    {
        var used = Math.Clamp(window.UsedPercent, 0, 100);
        var resetText = window.ResetsAt is long resetTimestamp
            ? $"Resets {DateTimeOffset.FromUnixTimeSeconds(resetTimestamp).ToLocalTime():MM-dd HH:mm}"
            : "Reset time unavailable";

        return new QuotaWindowViewModel
        {
            Label = window.Label,
            UsedPercent = used,
            UsedText = $"{used:0.0}%",
            ResetText = resetText,
            ProgressBrush = used switch
            {
                >= 90 => MediaBrushes.IndianRed,
                >= 70 => MediaBrushes.Goldenrod,
                _ => MediaBrushes.LightGreen,
            },
        };
    }
}
