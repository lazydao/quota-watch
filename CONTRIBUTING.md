# Contributing

Contributions should stay focused, preserve the existing style, and include targeted verification for changed behavior.

## Requirements

- Python 3.10 or later
- .NET 8 SDK for the Windows tray app
- Windows for building or running the tray app

## Python CLI setup

```powershell
py -3.10 -m venv .venv
.venv\Scripts\python -m pip install -e .
```

Run the CLI directly from the editable environment:

```powershell
.venv\Scripts\quota --help
.venv\Scripts\quota --json
```

## Verification

Run the Python tests:

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
```

Build and smoke-test the Windows tray app:

```powershell
dotnet build windows\QuotaWatch.Tray\QuotaWatch.Tray.csproj --configuration Release
dotnet run --project windows\QuotaWatch.Tray.Tests\QuotaWatch.Tray.Tests.csproj --configuration Release
```

Create a self-contained executable when the change affects packaging or Windows integration:

```powershell
.\windows\publish.ps1
```

The default output is `windows\artifacts\QuotaWatch.Tray-win-x64\QuotaWatch.Tray.exe`. The publish script also accepts `-Runtime win-arm64`, but GitHub Releases currently publish only the tested `win-x64` asset.

## Pull requests

- Keep code and documentation changes scoped to one purpose.
- Add or update tests when behavior changes.
- Do not commit generated files under `windows\artifacts`.
- Describe any verification that was not run.

Maintainers should follow [docs/releasing.md](docs/releasing.md) when publishing a version.
