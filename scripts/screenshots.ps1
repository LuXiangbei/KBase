param(
    [string]$Base = "http://127.0.0.1:8010",
    [int[]]$Widths = @(360, 768, 1280)
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root "screenshots"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$candidates = @(
    "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
$browser = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $browser = $c; break }
}
if (-not $browser) {
    Write-Output "browser not found"
    exit 2
}

# Headless has a minimum window width (~492 CSS px).
# The 360 case renders the app inside a same-origin iframe at exactly 360 px.
# 768/1280 compensate the 30 px window border.
function Get-Target {
    param([int]$Width)
    if ($Width -le 400) {
        return @{ Url = "$Base/_audit.html?w=$Width"; Window = 500 }
    }
    return @{ Url = "$Base/?audit=1&demo=1&expect=$Width"; Window = ($Width + 30) }
}

$failed = @()

foreach ($width in $Widths) {
    $target = Get-Target -Width $width
    $profile = Join-Path $env:TEMP ("kbase-edge-" + [guid]::NewGuid().ToString("N"))
    $shot = Join-Path $outDir ("kbase-" + $width + ".png")
    $domFile = Join-Path $env:TEMP ("kbase-dom-" + $width + ".html")
    $errFile = Join-Path $env:TEMP ("kbase-dom-" + $width + ".err")
    if (Test-Path $shot) { Remove-Item $shot -Force }

    $common = @(
        "--user-data-dir=$profile",
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--virtual-time-budget=12000",
        "--window-size=$($target.Window),950"
    )

    $shotArgs = $common + @("--screenshot=$shot", $target.Url)
    $p = Start-Process -FilePath $browser -ArgumentList $shotArgs -PassThru -WindowStyle Hidden
    $p | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue
    if (-not $p.HasExited) { $p.Kill() }
    for ($i = 0; $i -lt 40 -and -not (Test-Path $shot); $i++) { Start-Sleep -Milliseconds 250 }

    $domArgs = $common + @("--dump-dom", $target.Url)
    $p2 = Start-Process -FilePath $browser -ArgumentList $domArgs -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput $domFile -RedirectStandardError $errFile
    $p2 | Wait-Process -Timeout 30 -ErrorAction SilentlyContinue
    if (-not $p2.HasExited) { $p2.Kill() }

    $title = ""
    if (Test-Path $domFile) {
        $dom = Get-Content $domFile -Raw -Encoding utf8
        if ($dom -match "<title>(.*?)</title>") { $title = $Matches[1].Trim() }
    }

    $size = 0
    if (Test-Path $shot) { $size = (Get-Item $shot).Length }
    $label = "no title"
    if ($title) { $label = $title }
    Write-Output ("[{0}] {1} -> {2} ({3} bytes)" -f $width, $label, (Split-Path $shot -Leaf), $size)

    if ($title -match "w=(\d+) sw=(\d+) overflow=(-?\d+)") {
        $cssWidth = [int]$Matches[1]
        $overflow = [int]$Matches[3]
        if ([Math]::Abs($cssWidth - $width) -gt 2) {
            $failed += ("{0}: viewport mismatch (got {1}px)" -f $width, $cssWidth)
        }
        if ($overflow -gt 1) {
            $failed += ("{0}: horizontal overflow {1}px" -f $width, $overflow)
        }
    } else {
        $failed += ("{0}: audit title missing" -f $width)
    }
    if ($size -lt 5000) {
        $failed += ("{0}: screenshot missing or too small" -f $width)
    }
}

if ($failed.Count -gt 0) {
    Write-Output ("FAILED: " + ($failed -join "; "))
    exit 1
}
Write-Output "RESPONSIVE OK"
exit 0

