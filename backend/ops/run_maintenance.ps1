param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("export-cleanup", "creator-retention")]
    [string]$Job,

    [ValidateSet("development", "production")]
    [string]$Mode = "production",

    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$backend = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $EnvFile) {
    $EnvFile = Join-Path $backend ".env"
}
$envPath = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
if ($Mode -eq "production") {
    & (Join-Path $PSScriptRoot "validate_production_env.ps1") -EnvFile $envPath
}
$lockPath = Join-Path $backend ".maintenance-$Job.lock"
$lock = $null

try {
    $lock = [IO.File]::Open(
        $lockPath,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
}
catch [IO.IOException] {
    throw "Cannot acquire lock for '$Job': another run or a filesystem error."
}

$transcriptStarted = $false
try {
    $logDir = Join-Path $backend "ops/logs"
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $logPath = Join-Path $logDir ("{0}-{1}.log" -f $Job, (Get-Date -Format "yyyyMMdd-HHmmss"))
    Start-Transcript -LiteralPath $logPath | Out-Null
    $transcriptStarted = $true
    $composeArgs = @(
        "compose", "--env-file", $envPath,
        "-f", (Join-Path $backend "docker-compose.yml")
    )
    if ($Mode -eq "production") {
        $composeArgs += @("-f", (Join-Path $backend "docker-compose.production.yml"))
    }
    $composeArgs += @("--profile", "maintenance", "run", "--rm", "--no-deps", $Job)

    Push-Location $backend
    try {
        & docker @composeArgs
        if ($LASTEXITCODE -ne 0) {
            throw "Maintenance job '$Job' failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    try {
        if ($transcriptStarted) {
            Stop-Transcript | Out-Null
        }
    }
    finally {
        $lock.Dispose()
    }
}
