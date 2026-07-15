using System.Text.Json.Serialization;

namespace QuotaWatch.Tray.Models;

public sealed class QuotaSnapshot
{
    [JsonPropertyName("schema_version")]
    public int SchemaVersion { get; init; }

    [JsonPropertyName("generated_at")]
    public DateTimeOffset GeneratedAt { get; init; }

    [JsonPropertyName("providers")]
    public List<ProviderSnapshot> Providers { get; init; } = [];
}

public sealed class ProviderSnapshot
{
    [JsonPropertyName("provider")]
    public string Provider { get; init; } = string.Empty;

    [JsonPropertyName("status")]
    public string Status { get; init; } = string.Empty;

    [JsonPropertyName("buckets")]
    public List<QuotaBucket> Buckets { get; init; } = [];

    [JsonPropertyName("captured_at")]
    public DateTimeOffset CapturedAt { get; init; }

    [JsonPropertyName("message")]
    public string? Message { get; init; }
}

public sealed class QuotaBucket
{
    [JsonPropertyName("bucket_id")]
    public string BucketId { get; init; } = string.Empty;

    [JsonPropertyName("name")]
    public string Name { get; init; } = string.Empty;

    [JsonPropertyName("windows")]
    public List<QuotaWindow> Windows { get; init; } = [];

    [JsonPropertyName("plan_type")]
    public string? PlanType { get; init; }
}

public sealed class QuotaWindow
{
    [JsonPropertyName("label")]
    public string Label { get; init; } = string.Empty;

    [JsonPropertyName("used_percent")]
    public double UsedPercent { get; init; }

    [JsonPropertyName("window_minutes")]
    public int? WindowMinutes { get; init; }

    [JsonPropertyName("resets_at")]
    public long? ResetsAt { get; init; }
}
