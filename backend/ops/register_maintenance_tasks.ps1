param(
    [string]$EnvFile = "",
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
$backend = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$runner = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "run_maintenance.ps1")).Path
if (-not $EnvFile) {
    $EnvFile = Join-Path $backend ".env"
}
$envPath = (Resolve-Path -LiteralPath $EnvFile -ErrorAction Stop).Path
& (Join-Path $PSScriptRoot "validate_production_env.ps1") -EnvFile $envPath
if ($runner.Contains('"') -or $envPath.Contains('"')) {
    throw "Paths containing double quotes cannot be used in scheduled task arguments."
}

$specs = @(
    @{
        Name = "AMP Export Cleanup"
        Job = "export-cleanup"
        Trigger = New-ScheduledTaskTrigger -Daily -At "02:30"
    },
    @{
        Name = "AMP Creator Retention"
        Job = "creator-retention"
        Trigger = New-ScheduledTaskTrigger -Daily -At "03:30"
    }
)

foreach ($spec in $specs) {
    $existing = Get-ScheduledTask -TaskName $spec.Name -ErrorAction SilentlyContinue
    if ($existing -and -not $Replace) {
        throw "Scheduled task '$($spec.Name)' already exists. Use -Replace to update it."
    }
}

$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 4)

foreach ($spec in $specs) {
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        $runner + '" -Job ' + $spec.Job + ' -Mode production -EnvFile "' + $envPath + '"'
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
    Register-ScheduledTask -TaskName $spec.Name -Action $action -Trigger $spec.Trigger -Principal $principal -Settings $settings -Description "AMP backend maintenance: $($spec.Job)" -Force | Out-Null
    Write-Output "Registered $($spec.Name)"
}
