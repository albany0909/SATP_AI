# Register or remove the paper-only SATP desk watchdog scheduled task.
# Current user only. Does not embed a machine SID or a user name.
param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$taskName = "SATP desk watchdog"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $repo "tools\deskWatchdog.py"

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "UNINSTALLED $taskName"
    exit 0
}

function Resolve-Pythonw {
    if ($env:SATP_DESK_PYTHONW -and (Test-Path -LiteralPath $env:SATP_DESK_PYTHONW)) {
        return (Resolve-Path -LiteralPath $env:SATP_DESK_PYTHONW).Path
    }
    $found = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($found) {
        return $found.Source
    }
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($py) {
        $sibling = Join-Path (Split-Path -Parent $py.Source) "pythonw.exe"
        if (Test-Path -LiteralPath $sibling) {
            return $sibling
        }
    }
    throw "pythonw.exe not found. Set SATP_DESK_PYTHONW to the full path."
}

if (-not (Test-Path -LiteralPath $script)) {
    throw "watchdog script missing: $script"
}

$pythonw = Resolve-Pythonw
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$script`"" -WorkingDirectory $repo
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 3) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -Hidden `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$desc = "Paper-only SATP desk watchdog. Skips restart when config\data\desk_watchdog.pause exists. Does not change settings.json."

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($repeat, $logon) `
    -Settings $settings -Principal $principal -Description $desc -Force | Out-Null
Write-Output "INSTALLED $taskName"
Write-Output "pythonw $pythonw"
Write-Output "script $script"
Write-Output "workdir $repo"
Write-Output "user $user"
