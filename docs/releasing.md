# Releasing Quota Watch

Quota Watch uses matching versions for the Python CLI, Windows tray project, Git tag, and release assets. Do not move or reuse a published version tag; fix release problems in a new patch version.

## 1. Check `main`

Make sure everything for the release is merged and CI is green on `main`. Review the user-facing changes that will be summarized in the GitHub Release.

For changes to the tray app, verify on Windows before releasing:

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
dotnet build windows\QuotaWatch.Tray\QuotaWatch.Tray.csproj --configuration Release
dotnet run --project windows\QuotaWatch.Tray.Tests\QuotaWatch.Tray.Tests.csproj --configuration Release
.\windows\publish.ps1
```

Confirm that the published EXE opens, refreshes through the installed matching `quota` CLI, and exits cleanly.

## 2. Run the Release workflow

On GitHub, open **Actions → Release → Run workflow**, keep the `main` branch, enter the version (for example `0.1.9`), and run it. The workflow:

1. Sets the version with `scripts/set_version.py` in `pyproject.toml`, `src/quota_watch/__init__.py`, `windows/QuotaWatch.Tray/QuotaWatch.Tray.csproj`, and the install examples in `README.md`.
2. Runs the Python tests and commits `Release <version>` to `main` when anything changed.
3. Builds the tray app on a Windows runner and runs the tray smoke tests.
4. Publishes a self-contained `win-x64` executable and packages it with the README and license.
5. Creates the annotated tag `v<version>` on that commit, only after the steps above passed.
6. Publishes the ZIP and SHA-256 checksum to the GitHub Release.

If a step fails before the tag is created, fix the problem on `main` and run the workflow again with the same version. Running it with a version whose tag already exists publishes that tag without changing `main`, which recovers a release that failed after tagging.

The workflow pushes to `main` with the repository's Actions token. If branch protection blocks that push, set the version in a pull request instead (`python scripts/set_version.py 0.1.9`), merge it, and run the workflow: it then finds nothing to commit and continues.

A run with an empty version creates a 14-day Actions artifact and does not tag or publish anything.

## 3. Manual alternative

Pushing an annotated tag still publishes a release. The version files must already match the tag, or the workflow rejects it:

```powershell
python scripts\set_version.py 0.1.9
git commit --all --message "Release 0.1.9"
git push origin main
git tag -a v0.1.9 -m "Quota Watch v0.1.9"
git push origin v0.1.9
```

## 4. Finish the release

After the workflow succeeds:

- Download the ZIP and verify its published SHA-256 checksum.
- Confirm the EXE version, icon, startup toggle, CLI discovery, and flyout refresh on Windows.
- Confirm that the Release page contains both the ZIP and checksum.
- Edit the generated Release notes into a short user-facing summary with installation or upgrade caveats when needed.

If the published artifact is defective, leave the existing tag immutable and publish the correction under a new patch version.
