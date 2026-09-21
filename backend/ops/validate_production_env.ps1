param(
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"
$backend = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $EnvFile) { $EnvFile = Join-Path $backend ".env" }
$envPath = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
$values = @{}

foreach ($rawLine in [IO.File]::ReadAllLines($envPath)) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith("#")) { continue }
    $separator = $line.IndexOf("=")
    if ($separator -lt 1) { throw "Invalid environment entry; expected NAME=value." }
    $name = $line.Substring(0, $separator).Trim()
    $value = $line.Substring($separator + 1).Trim()
    if ($value.Length -ge 2 -and (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
    )) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    if ($values.ContainsKey($name)) { throw "Duplicate environment variable: $name" }
    $values[$name] = $value
}

$secretNames = @(
    "DB_MIGRATION_PASSWORD", "DB_API_PASSWORD", "DB_SCHEDULER_PASSWORD",
    "DB_EMAIL_WORKER_PASSWORD", "DB_YOUTUBE_WORKER_PASSWORD",
    "DB_CALCULATION_WORKER_PASSWORD", "DB_EXPORT_WORKER_PASSWORD",
    "DB_RETENTION_WORKER_PASSWORD", "DB_LIFECYCLE_WORKER_PASSWORD",
    "DB_BACKUP_PASSWORD", "DB_MONITOR_PASSWORD", "MINIO_ROOT_PASSWORD",
    "S3_API_SECRET_KEY", "S3_WORKER_SECRET_KEY", "S3_BACKUP_SECRET_KEY",
    "S3_RESTORE_SECRET_KEY"
)

foreach ($name in $secretNames) {
    $value = $values[$name]
    if (-not $value) { throw "Missing production secret: $name" }
    if ($value.Length -lt 24) {
        throw "Production secret $name must be at least 24 characters."
    }
    if ($value -notmatch '^[A-Za-z0-9._~-]+$') {
        throw "Production secret $name must be URL-safe."
    }
    if ($value.ToLowerInvariant().StartsWith("replace-")) {
        throw "Production secret $name still contains an example value."
    }
}

$duplicates = $secretNames | Group-Object { $values[$_] } | Where-Object Count -gt 1
if ($duplicates) { throw "Database and storage secrets must not be reused." }

$jwt = $values["JWT_SECRET"]
if (-not $jwt -or [Text.Encoding]::UTF8.GetByteCount($jwt) -lt 48) {
    throw "JWT_SECRET must contain at least 48 bytes."
}
if ($jwt.ToLowerInvariant() -match '^(replace-|local-development|test-secret)') {
    throw "JWT_SECRET still contains an example or development value."
}

$frontend = $null
if (-not [Uri]::TryCreate($values["FRONTEND_URL"], [UriKind]::Absolute, [ref]$frontend) -or
    $frontend.Scheme -ne "https" -or
    $frontend.Host -in @("localhost", "127.0.0.1")) {
    throw "FRONTEND_URL must be a public HTTPS URL."
}

foreach ($prefix in @("", "MONITOR_")) {
    $hostName = $values["${prefix}SMTP_HOST"]
    $fromEmail = $values["${prefix}SMTP_FROM_EMAIL"]
    $useTls = $values["${prefix}SMTP_USE_TLS"] -eq "true"
    $useSsl = $values["${prefix}SMTP_USE_SSL"] -eq "true"
    if (-not $hostName -or $hostName -in @("mailpit", "localhost", "127.0.0.1")) {
        throw "${prefix}SMTP_HOST must point to a real mail service."
    }
    if ($useTls -eq $useSsl) {
        throw "Exactly one of ${prefix}SMTP_USE_TLS and ${prefix}SMTP_USE_SSL must be true."
    }
    if (-not $fromEmail -or $fromEmail.ToLowerInvariant().EndsWith(".test") -or
        $fromEmail.ToLowerInvariant().EndsWith(".local")) {
        throw "${prefix}SMTP_FROM_EMAIL must use a real domain."
    }
    $username = $values["${prefix}SMTP_USERNAME"]
    $password = $values["${prefix}SMTP_PASSWORD"]
    if ([bool]$username -ne [bool]$password) {
        throw "${prefix}SMTP_USERNAME and ${prefix}SMTP_PASSWORD must be set together."
    }
}

if (-not $values["MONITOR_ALERT_EMAIL"] -or
    $values["MONITOR_ALERT_EMAIL"].ToLowerInvariant().EndsWith(".test") -or
    $values["MONITOR_ALERT_EMAIL"].ToLowerInvariant().EndsWith(".local")) {
    throw "MONITOR_ALERT_EMAIL must use a real address."
}

Write-Output "Production environment validation passed."
