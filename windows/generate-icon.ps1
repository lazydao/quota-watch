[CmdletBinding()]
param(
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

if (-not $OutputPath) {
    $OutputPath = Join-Path $PSScriptRoot "QuotaWatch.Tray\Assets\QuotaWatch.ico"
}
$OutputPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = Split-Path -Parent $OutputPath
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$sizes = @(16, 20, 24, 32, 48, 64, 128, 256)
$frames = foreach ($size in $sizes) {
    $scale = $size / 32.0
    $bitmap = [System.Drawing.Bitmap]::new(
        $size,
        $size,
        [System.Drawing.Imaging.PixelFormat]::Format32bppArgb
    )
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.Clear([System.Drawing.Color]::Transparent)

    $background = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(35, 39, 55))
    $ring = [System.Drawing.Pen]::new(
        [System.Drawing.Color]::FromArgb(232, 234, 246),
        [single][Math]::Max(1.0, 3.2 * $scale)
    )
    $status = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(113, 220, 143))
    try {
        $graphics.FillEllipse($background, 2 * $scale, 2 * $scale, 28 * $scale, 28 * $scale)
        $graphics.DrawEllipse($ring, 5 * $scale, 5 * $scale, 20 * $scale, 20 * $scale)
        $graphics.DrawLine($ring, 20 * $scale, 20 * $scale, 27 * $scale, 27 * $scale)
        $graphics.FillEllipse($status, 21 * $scale, 2 * $scale, 9 * $scale, 9 * $scale)

        $memory = [System.IO.MemoryStream]::new()
        $bitmap.Save($memory, [System.Drawing.Imaging.ImageFormat]::Png)
        ,$memory.ToArray()
        $memory.Dispose()
    }
    finally {
        $status.Dispose()
        $ring.Dispose()
        $background.Dispose()
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

$stream = [System.IO.File]::Open($OutputPath, [System.IO.FileMode]::Create)
$writer = [System.IO.BinaryWriter]::new($stream)
try {
    $writer.Write([uint16]0)
    $writer.Write([uint16]1)
    $writer.Write([uint16]$frames.Count)

    $imageOffset = 6 + 16 * $frames.Count
    for ($index = 0; $index -lt $frames.Count; $index++) {
        $size = $sizes[$index]
        $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
        $writer.Write([byte]$(if ($size -eq 256) { 0 } else { $size }))
        $writer.Write([byte]0)
        $writer.Write([byte]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]32)
        $writer.Write([uint32]$frames[$index].Length)
        $writer.Write([uint32]$imageOffset)
        $imageOffset += $frames[$index].Length
    }

    foreach ($frame in $frames) {
        $writer.Write($frame)
    }
}
finally {
    $writer.Dispose()
    $stream.Dispose()
}

Write-Host "Generated application icon at $OutputPath"
