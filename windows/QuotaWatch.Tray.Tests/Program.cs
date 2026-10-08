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

var desktopFallback = QuotaSnapshotParser.Parse("""
{"schema_version":1,"providers":[{"provider":"claude","status":"stale",
"captured_at":"2026-10-08T04:12:29Z",
"message":"Claude Desktop fallback (28 min old). Reset times, model quotas and plan unavailable.",
"buckets":[{"bucket_id":"claude","name":"All models","plan_type":null,
"windows":[{"label":"5h","used_percent":9,"window_minutes":300,"resets_at":null},
{"label":"7d","used_percent":4,"window_minutes":10080,"resets_at":null}]}]}]}
""");
var desktopDashboard = new DashboardViewModel();
desktopDashboard.ApplySnapshot(desktopFallback);
var desktopProvider = desktopDashboard.Providers[0];
Assert(desktopProvider.StatusLabel == "Stale", "old desktop history is not live");
Assert(desktopProvider.Message.Contains("Claude Desktop fallback", StringComparison.Ordinal), "desktop source message");
Assert(desktopProvider.PlanText == string.Empty, "missing desktop plan is unknown");
Assert(desktopProvider.Buckets.Count == 1, "missing desktop model quotas are not invented");
Assert(desktopProvider.Buckets[0].Windows.Count == 2, "desktop all-model windows");
Assert(desktopProvider.Buckets[0].Windows.All(quotaWindow => quotaWindow.TimeText == "Time —"), "desktop time is unknown");
Assert(desktopProvider.Buckets[0].Windows.All(quotaWindow => quotaWindow.ResetText == "Reset time unavailable"), "desktop reset is unknown");

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
var sparkSnapshot = QuotaSnapshotParser.Parse("""
{"schema_version":1,"providers":[{"provider":"codex","status":"available","buckets":[
{"bucket_id":"codex","name":"Codex","windows":[{"label":"1w","used_percent":10}]},
{"bucket_id":"codex_bengalfox","name":"GPT-5.3-Codex-Spark","windows":[{"label":"1w","used_percent":100}]}]}]}
""");
dashboard.ApplySnapshot(sparkSnapshot);
Assert(dashboard.Providers[0].Buckets.Count == 1, "Spark hidden from panel");
Assert(dashboard.Severity == QuotaSeverity.Normal, "hidden Spark does not trigger warning");
Assert(dashboard.TrayTooltip.Contains("Codex 10.0%", StringComparison.Ordinal), "tooltip uses visible quota");
Assert(sparkSnapshot.Providers[0].Buckets.Count == 2, "raw Spark data preserved");

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
