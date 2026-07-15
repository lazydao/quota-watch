# Releasing Quota Watch

Quota Watch uses matching versions for the Python CLI, Windows tray project, Git tag, and release assets. Do not move or reuse a published version tag; fix release problems in a new patch version.

## 1. Prepare the version

Start from an up-to-date, clean `main` branch. Update the same version in:

- `pyproject.toml`
- `windows/QuotaWatch.Tray/QuotaWatch.Tray.csproj`
- the stable-version examples in `README.md`

Review the user-facing changes that will be summarized in the GitHub Release.

## 2. Verify locally

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
dotnet build windows\QuotaWatch.Tray\QuotaWatch.Tray.csproj --configuration Release
dotnet run --project windows\QuotaWatch.Tray.Tests\QuotaWatch.Tray.Tests.csproj --configuration Release
.\windows\publish.ps1
```

Confirm that the published EXE opens, refreshes through the installed matching `quota` CLI, and exits cleanly.

## 3. Commit and tag

Commit the version change, push `main`, and then create an annotated tag. For example:

```powershell
git push origin main
git tag -a v0.1.6 -m "Quota Watch v0.1.6"
git push origin v0.1.6
```

The tag-driven Release workflow rejects versions that do not match both project files.

## 4. Verify GitHub Actions

The workflow:

1. Builds the tray app on a Windows runner.
2. Runs the tray smoke tests.
3. Publishes a self-contained `win-x64` executable.
4. Packages the EXE with the README and license.
5. Publishes the ZIP and SHA-256 checksum to the GitHub Release.

A manual workflow run without a release tag creates a 14-day Actions artifact and does not publish a GitHub Release.

## 5. Finish the release

After the workflow succeeds:

- Download the ZIP and verify its published SHA-256 checksum.
- Confirm the EXE version, icon, startup toggle, CLI discovery, and flyout refresh on Windows.
- Confirm that the Release page contains both the ZIP and checksum.
- Edit the generated Release notes into a short user-facing summary with installation or upgrade caveats when needed.

If the published artifact is defective, leave the existing tag immutable and publish the correction under a new patch version.
