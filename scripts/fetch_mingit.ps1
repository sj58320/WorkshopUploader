$ErrorActionPreference = 'Stop'

$releaseTag = 'v2.55.0.windows.3'
$asset = 'MinGit-2.55.0.3-64-bit.zip'
$expectedSha256 = 'f48e2d2dc74a24454adc6d8fd0ac25bf9c2386f19cfb06202b9465aaad4f9f05'
$url = "https://github.com/git-for-windows/git/releases/download/$releaseTag/$asset"
$repoRoot = Split-Path -Parent $PSScriptRoot
$vendorRoot = Join-Path $repoRoot '.vendor'
$zipPath = Join-Path $vendorRoot $asset
$downloadPath = "$zipPath.download"
$extractPath = Join-Path $vendorRoot 'MinGit'
$stagingPath = Join-Path $vendorRoot 'MinGit.extracting'
$stagingGitExe = Join-Path $stagingPath 'cmd\git.exe'
$gitExe = Join-Path $extractPath 'cmd\git.exe'

$vendorFullPath = [IO.Path]::GetFullPath($vendorRoot).TrimEnd('\') + '\'
foreach ($candidatePath in @($extractPath, $stagingPath)) {
    $candidateFullPath = [IO.Path]::GetFullPath($candidatePath)
    if (-not $candidateFullPath.StartsWith($vendorFullPath, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a MinGit path outside .vendor: $candidateFullPath"
    }
}

New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
if (-not (Test-Path -LiteralPath $zipPath)) {
    if (Test-Path -LiteralPath $downloadPath) {
        Remove-Item -LiteralPath $downloadPath -Force
    }
    Invoke-WebRequest -Uri $url -OutFile $downloadPath
    Move-Item -LiteralPath $downloadPath -Destination $zipPath
}

$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $zipPath).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw "MinGit SHA-256 mismatch: expected $expectedSha256, got $actualSha256"
}

if (Test-Path -LiteralPath $stagingPath) {
    Remove-Item -LiteralPath $stagingPath -Recurse -Force
}
Expand-Archive -LiteralPath $zipPath -DestinationPath $stagingPath -Force
if (-not (Test-Path -LiteralPath $stagingGitExe)) {
    throw "MinGit extraction did not create cmd\git.exe: $stagingGitExe"
}
if (Test-Path -LiteralPath $extractPath) {
    Remove-Item -LiteralPath $extractPath -Recurse -Force
}
Move-Item -LiteralPath $stagingPath -Destination $extractPath
if (-not (Test-Path -LiteralPath $gitExe)) {
    throw "MinGit finalization did not create cmd\git.exe: $gitExe"
}

Write-Output $extractPath
