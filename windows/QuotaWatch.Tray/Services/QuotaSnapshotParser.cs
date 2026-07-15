using System.Text.Json;
using QuotaWatch.Tray.Models;

namespace QuotaWatch.Tray.Services;

public static class QuotaSnapshotParser
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
    };

    public static QuotaSnapshot Parse(string json)
    {
        QuotaSnapshot? snapshot;
        try
        {
            snapshot = JsonSerializer.Deserialize<QuotaSnapshot>(json, JsonOptions);
        }
        catch (JsonException error)
        {
            throw new InvalidOperationException("The quota CLI returned invalid JSON.", error);
        }

        if (snapshot is null || snapshot.SchemaVersion != 1)
        {
            throw new InvalidOperationException("The quota CLI returned an unsupported JSON schema.");
        }

        return snapshot;
    }
}
