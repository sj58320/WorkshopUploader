param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$clientId = [Environment]::GetEnvironmentVariable('WORKSHOP_UPLOADER_GITHUB_CLIENT_ID')
if ([String]::IsNullOrWhiteSpace($clientId)) {
    throw 'WORKSHOP_UPLOADER_GITHUB_CLIENT_ID must be set before building a release.'
}

$fetchMinGitScript = Join-Path $PSScriptRoot 'fetch_mingit.ps1'
if (-not (Test-Path -LiteralPath $fetchMinGitScript)) {
    throw "MinGit fetch script was not found: $fetchMinGitScript"
}
$minGitRoot = & $fetchMinGitScript
$minGitExe = Join-Path $minGitRoot 'cmd\git.exe'
if (-not (Test-Path -LiteralPath $minGitExe)) {
    throw "Bundled Git executable was not found: $minGitExe"
}

$vendorRoot = Join-Path $repoRoot '.vendor'
$githubConfigPath = Join-Path $vendorRoot 'github_app.json'
$githubConfig = [ordered]@{
    client_id = $clientId.Trim()
    repository_id = 1157838808
} | ConvertTo-Json -Compress
$utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText($githubConfigPath, $githubConfig, $utf8WithoutBom)

$buildRoot = Join-Path $repoRoot 'build'
$distRoot = Join-Path $repoRoot 'dist'
$releaseExe = Join-Path $distRoot "WorkshopUploader-$Version.exe"
$pyInstallerDist = Join-Path $buildRoot 'pyinstaller-dist'
$pyInstallerWork = Join-Path $buildRoot 'pyinstaller-work'
$pyInstallerSpec = Join-Path $buildRoot 'pyinstaller-spec'

$repoFullPath = [IO.Path]::GetFullPath($repoRoot).TrimEnd('\') + '\'
$buildFullPath = [IO.Path]::GetFullPath($buildRoot)
if (-not $buildFullPath.StartsWith($repoFullPath, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to use a build directory outside the repository: $buildFullPath"
}

if (Test-Path -LiteralPath $buildRoot) {
    Remove-Item -LiteralPath $buildRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $buildRoot, $distRoot | Out-Null

$pyInstallerArgs = @(
    '-m',
    'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onefile',
    '--windowed',
    '--noupx',
    '--log-level',
    'WARN',
    '--name',
    'WorkshopUploader',
    '--distpath',
    $pyInstallerDist,
    '--workpath',
    $pyInstallerWork,
    '--specpath',
    $pyInstallerSpec,
    '--paths',
    $repoRoot,
    '--hidden-import',
    'scripts.SteamWorks_Workshop_warpper',
    '--collect-submodules',
    'steamworks',
    '--add-binary',
    "$repoRoot\SteamworksPy64.dll;.",
    '--add-binary',
    "$repoRoot\steam_api64.dll;.",
    '--add-binary',
    "$repoRoot\scripts\VPKEdit\vpkeditcli.exe;scripts\VPKEdit",
    '--add-data',
    "$repoRoot\steam_appid.txt;.",
    '--add-data',
    "$repoRoot\THIRD_PARTY_NOTICES.md;.",
    '--add-data',
    "$repoRoot\scripts\VPKEdit\LICENSE;scripts\VPKEdit",
    '--add-data',
    "$repoRoot\scripts\VPKEdit\CREDITS.md;scripts\VPKEdit",
    '--add-data',
    "$repoRoot\third_party\SteamworksPy\LICENSE;third_party\SteamworksPy",
    '--add-data',
    "$repoRoot\third_party\PyInstaller\COPYING.txt;third_party\PyInstaller",
    '--add-data',
    "$minGitRoot;mingit",
    '--add-data',
    "$githubConfigPath;.",
    (Join-Path $repoRoot 'workshop_uploader_gui.py')
)

& python @pyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$builtExe = Join-Path $pyInstallerDist 'WorkshopUploader.exe'
if (-not (Test-Path -LiteralPath $builtExe)) {
    throw "GUI executable was not created: $builtExe"
}

if (Test-Path -LiteralPath $releaseExe) {
    Remove-Item -LiteralPath $releaseExe -Force
}
Copy-Item -LiteralPath $builtExe -Destination $releaseExe
Remove-Item -LiteralPath $buildRoot -Recurse -Force

Write-Output $releaseExe
