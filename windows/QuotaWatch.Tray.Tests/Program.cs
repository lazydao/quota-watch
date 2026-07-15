using QuotaWatch.Tray.Services;
using QuotaWatch.Tray.ViewModels;

const string validJson = """
{
  "schema_version": 1,
  "generated_at": "2026-07-15T09:30:27.101745+00:00",
  "providers": [
    {
      "provider": "claude",
      "status": "stale",
      "buckets": [
        {
          "bucket_id": "claude",
          "name": "Claude",
          "windows": [
            {
              "label": "5h",
              "used_percent": 72.5,
              "window_minutes": 300,
              "resets_at": 1784118000
            }
          ],
          "plan_type": null
        }
      ],
      "captured_at": "2026-07-15T08:36:27.333446+00:00",
      "message": "Last Claude update is stale."
    }
  ]
}
""";

var snapshot = QuotaSnapshotParser.Parse(validJson);
Assert(snapshot.SchemaVersion == 1, "schema version");
Assert(snapshot.Providers.Count == 1, "provider count");
Assert(snapshot.Providers[0].Buckets[0].Windows[0].UsedPercent == 72.5, "used percentage");

var dashboard = new DashboardViewModel();
dashboard.ApplySnapshot(snapshot);
Assert(dashboard.Providers[0].Name == "Claude", "provider display name");
Assert(dashboard.Severity == QuotaSeverity.Warning, "stale provider severity");
Assert(dashboard.TrayTooltip.Contains("Claude 72.5%", StringComparison.Ordinal), "tray tooltip");

var unsupportedSchemaRejected = false;
try
{
    QuotaSnapshotParser.Parse("""{"schema_version":2,"generated_at":"2026-07-15T00:00:00Z","providers":[]}""");
}
catch (InvalidOperationException)
{
    unsupportedSchemaRejected = true;
}

Assert(unsupportedSchemaRejected, "unsupported schema rejection");
Console.WriteLine("QuotaWatch.Tray smoke tests passed.");

static void Assert(bool condition, string description)
{
    if (!condition)
    {
        throw new InvalidOperationException($"Assertion failed: {description}");
    }
}
