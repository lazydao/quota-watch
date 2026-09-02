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
Assert(dashboard.Severity == QuotaSeverity.Critical, "pace-based dashboard severity");
Assert(dashboard.TrayTooltip.Contains("Claude 72.5%", StringComparison.Ordinal), "tray tooltip");
var window = dashboard.Providers[0].Buckets[0].Windows[0];
Assert(window.HasElapsedTime, "elapsed time availability");
Assert(Math.Abs(window.ElapsedPercent - 43.48) < 0.01, "elapsed time percentage");
Assert(window.TimeText == "Time 43.5%", "elapsed time label");
Assert(window.Severity == QuotaSeverity.Critical, "critical pacing severity");

var generatedAt = DateTimeOffset.Parse("2026-08-13T10:00:00Z");
var resetAt = generatedAt.AddMinutes(80).ToUnixTimeSeconds();
AssertWindowSeverity(22, 100, resetAt, generatedAt, QuotaSeverity.Normal, "two-point buffer");
AssertWindowSeverity(22.1, 100, resetAt, generatedAt, QuotaSeverity.Warning, "warning above buffer");
AssertWindowSeverity(35, 100, resetAt, generatedAt, QuotaSeverity.Critical, "critical at fifteen points");
AssertWindowSeverity(100, 100, generatedAt.ToUnixTimeSeconds(), generatedAt, QuotaSeverity.Critical, "exhausted quota");

var missingTime = QuotaWindowViewModel.FromSnapshot(
    new QuotaWatch.Tray.Models.QuotaWindow { Label = "1w", UsedPercent = 80 },
    generatedAt
);
Assert(!missingTime.HasElapsedTime, "missing time availability");
Assert(missingTime.TimeText == "Time —", "missing time label");
Assert(missingTime.Severity == QuotaSeverity.Warning, "missing time fallback warning");

var invalidTime = QuotaWindowViewModel.FromSnapshot(
    new QuotaWatch.Tray.Models.QuotaWindow
    {
        Label = "1w",
        UsedPercent = 95,
        WindowMinutes = 0,
        ResetsAt = long.MaxValue,
    },
    generatedAt
);
Assert(!invalidTime.HasElapsedTime, "invalid time availability");
Assert(invalidTime.ResetText == "Reset time unavailable", "invalid reset label");
Assert(invalidTime.Severity == QuotaSeverity.Critical, "invalid time fallback critical");

var invalidUsage = QuotaWindowViewModel.FromSnapshot(
    new QuotaWatch.Tray.Models.QuotaWindow { Label = "1w", UsedPercent = double.NaN },
    generatedAt
);
Assert(invalidUsage.UsedPercent == 0, "invalid usage normalization");
Assert(invalidUsage.Severity == QuotaSeverity.Normal, "invalid usage safe severity");

var warningDashboard = new DashboardViewModel();
warningDashboard.ApplySnapshot(CreateSnapshot(22.1, 100, resetAt, generatedAt));
Assert(warningDashboard.Severity == QuotaSeverity.Warning, "warning tray severity");

var normalDashboard = new DashboardViewModel();
normalDashboard.ApplySnapshot(CreateSnapshot(22, 100, resetAt, generatedAt));
Assert(normalDashboard.Severity == QuotaSeverity.Normal, "normal tray severity");

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

static void AssertWindowSeverity(
    double usedPercent,
    int windowMinutes,
    long resetsAt,
    DateTimeOffset generatedAt,
    QuotaSeverity expected,
    string description
)
{
    var viewModel = QuotaWindowViewModel.FromSnapshot(
        new QuotaWatch.Tray.Models.QuotaWindow
        {
            Label = "test",
            UsedPercent = usedPercent,
            WindowMinutes = windowMinutes,
            ResetsAt = resetsAt,
        },
        generatedAt
    );
    Assert(viewModel.Severity == expected, description);
}

static QuotaWatch.Tray.Models.QuotaSnapshot CreateSnapshot(
    double usedPercent,
    int windowMinutes,
    long resetsAt,
    DateTimeOffset generatedAt
)
{
    return new QuotaWatch.Tray.Models.QuotaSnapshot
    {
        SchemaVersion = 1,
        GeneratedAt = generatedAt,
        Providers =
        [
            new QuotaWatch.Tray.Models.ProviderSnapshot
            {
                Provider = "test",
                Status = "available",
                Buckets =
                [
                    new QuotaWatch.Tray.Models.QuotaBucket
                    {
                        BucketId = "test",
                        Name = "Test",
                        Windows =
                        [
                            new QuotaWatch.Tray.Models.QuotaWindow
                            {
                                Label = "test",
                                UsedPercent = usedPercent,
                                WindowMinutes = windowMinutes,
                                ResetsAt = resetsAt,
                            },
                        ],
                    },
                ],
            },
        ],
    };
}
