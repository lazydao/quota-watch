[CmdletBinding()]
param(
    [ValidateSet("win-x64", "win-arm64")]
    [string]$Runtime = "win-x64",

    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$project = Join-Path $PSScriptRoot "QuotaWatch.Tray\QuotaWatch.Tray.csproj"
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot "artifacts\QuotaWatch.Tray-$Runtime"
}

dotnet publish $project `
    --configuration Release `
    --runtime $Runtime `
    --self-contained true `
    --output $OutputDirectory `
    -p:PublishSingleFile=true `
    -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:EnableCompressionInSingleFile=true `
    -p:PublishTrimmed=false `
    -p:DebugType=None `
    -p:DebugSymbols=false

if ($LASTEXITCODE -ne 0) {
    throw "dotnet publish failed with exit code $LASTEXITCODE."
}

$executable = Join-Path $OutputDirectory "QuotaWatch.Tray.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Published executable was not found at $executable."
}

Write-Host "Published Quota Watch Tray to $executable"
