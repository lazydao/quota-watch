using System.IO;
using System.Text.Json;
using QuotaWatch.Tray.Models;

namespace QuotaWatch.Tray.Services;

public sealed class SnapshotCache
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
    };

    private readonly string _cachePath;

    public SnapshotCache()
    {
        var cacheDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "QuotaWatch");
        _cachePath = Path.Combine(cacheDirectory, "tray-snapshot.json");
    }

    public async Task<QuotaSnapshot?> LoadAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            if (!File.Exists(_cachePath))
            {
                return null;
            }

            await using var stream = File.OpenRead(_cachePath);
            var snapshot = await JsonSerializer.DeserializeAsync<QuotaSnapshot>(
                stream,
                JsonOptions,
                cancellationToken);
            return snapshot?.SchemaVersion == 1 ? snapshot : null;
        }
        catch (IOException)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }
        catch (JsonException)
        {
            return null;
        }
    }

    public async Task SaveAsync(QuotaSnapshot snapshot, CancellationToken cancellationToken = default)
    {
        var directory = Path.GetDirectoryName(_cachePath)!;
        Directory.CreateDirectory(directory);
        var temporaryPath = _cachePath + ".tmp";

        await using (var stream = File.Create(temporaryPath))
        {
            await JsonSerializer.SerializeAsync(stream, snapshot, JsonOptions, cancellationToken);
        }

        File.Move(temporaryPath, _cachePath, overwrite: true);
    }
}
