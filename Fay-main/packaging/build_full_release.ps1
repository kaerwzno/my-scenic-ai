param(
    [string]$MainPython = "C:\Users\Lenovo\anaconda3\envs\fay312\python.exe",
    [string]$LegacyPython = "C:\Users\Lenovo\anaconda3\envs\fay38\python.exe",
    [string]$Iscc = ""
)

$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $projectRoot
$releaseMemoryDir = Join-Path $projectRoot "packaging\release\memory"
$normalIssPath = Join-Path $projectRoot "packaging\inno\fay.iss"
$legacyIssPath = Join-Path $projectRoot "packaging\inno\fay-legacy.iss"

function Get-IssDefineValue {
    param(
        [string]$Path,
        [string]$DefineName
    )

    $match = Select-String -Path $Path -Pattern "^\s*#define\s+$DefineName\s+`"(.+)`"\s*$" | Select-Object -First 1
    if (-not $match) {
        throw "Missing #define $DefineName in $Path"
    }
    return $match.Matches[0].Groups[1].Value
}

function Get-IsccPath {
    param([string]$ExplicitPath)

    if ($ExplicitPath -and (Test-Path $ExplicitPath)) {
        return $ExplicitPath
    }

    $candidates = @(
        (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue),
        "C:\Users\Lenovo\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) }

    return $candidates | Select-Object -First 1
}

function Assert-Tool {
    param(
        [string]$Path,
        [string]$Name
    )

    if (-not (Test-Path $Path)) {
        throw "$Name not found: $Path"
    }
}

function Invoke-Step {
    param([string[]]$Command)

    $commandLine = ($Command | ForEach-Object {
        if ($_ -match "\s") { '"{0}"' -f $_ } else { $_ }
    }) -join " "
    Write-Host $commandLine
    & $Command[0] $Command[1..($Command.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $commandLine"
    }
}

Assert-Tool -Path $MainPython -Name "Main Python"
Assert-Tool -Path $LegacyPython -Name "Legacy Python"

$isccPath = Get-IsccPath -ExplicitPath $Iscc
if (-not $isccPath) {
    throw "ISCC.exe not found"
}

Write-Host "Restoring memory directory to Git HEAD..."
Invoke-Step -Command @("git", "restore", "--source=HEAD", "--worktree", "--staged", "--", "memory")
Invoke-Step -Command @("git", "clean", "-fd", "--", "memory")

if (Test-Path $releaseMemoryDir) {
    Remove-Item -Recurse -Force $releaseMemoryDir
}
Write-Host "Exporting Git memory snapshot for release packaging..."
Invoke-Step -Command @(
    "git",
    "checkout-index",
    "--force",
    "--prefix=packaging/release/",
    "--",
    "memory/fay.db",
    "memory/user_profiles.db"
)

Write-Host "Syncing release MCP config from Git baseline..."
Invoke-Step -Command @($MainPython, "packaging\\sync_release_mcp_config.py")

Write-Host "Building normal package..."
Invoke-Step -Command @($MainPython, "-m", "PyInstaller", "--clean", "--noconfirm", "fay.spec")

Write-Host "Building legacy package..."
Invoke-Step -Command @($LegacyPython, "-m", "PyInstaller", "--clean", "--noconfirm", "fay-legacy.spec")

Write-Host "Building installers..."
Invoke-Step -Command @($isccPath, "packaging\inno\fay.iss")
Invoke-Step -Command @($isccPath, "packaging\inno\fay-legacy.iss")

$normalVersion = Get-IssDefineValue -Path $normalIssPath -DefineName "MyAppVersion"
$legacyVersion = Get-IssDefineValue -Path $legacyIssPath -DefineName "MyAppVersion"
$normalInstaller = "dist\installer\FaySetup-$normalVersion.exe"
$legacyInstaller = "dist\installer\FaySetup-$legacyVersion-legacy.exe"

Write-Host "Installer hashes:"
Get-FileHash $normalInstaller -Algorithm SHA256 | Format-Table -AutoSize
Get-FileHash $legacyInstaller -Algorithm SHA256 | Format-Table -AutoSize
