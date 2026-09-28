param(
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$backend = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $EnvFile) { $EnvFile = Join-Path $backend ".env" }
python (Join-Path $PSScriptRoot "validate_production_env.py") --env-file $EnvFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
