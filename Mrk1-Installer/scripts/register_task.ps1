# =============================================================================
# FILE: Mrk1-Installer/scripts/register_task.ps1
# -----------------------------------------------------------------------------
# Run by the installer (with Administrator rights) to create the Windows task
# "LanWatch". The task starts LanWatch with Administrator rights WITHOUT a
# permission prompt each time; LanWatch needs those rights to scan and to
# measure traffic.
#
#   -AutoStart   also start LanWatch whenever this user signs in to Windows
#                (the "Start LanWatch automatically" option in the wizard)
#
# Even without -AutoStart the task is created: the LanWatch shortcuts use it
# to start LanWatch when it isn't running (LanWatch.exe --open).
# =============================================================================
param(
    [Parameter(Mandatory = $true)][string]$AppDir,
    [switch]$AutoStart
)
$ErrorActionPreference = "Stop"

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute (Join-Path $AppDir "LanWatch.exe") -Argument "--background" -WorkingDirectory $AppDir
# Interactive + Highest = runs as this user, with Administrator rights, while signed in
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

$task = @{
    TaskName    = "LanWatch"
    Description = "Starts LanWatch (home network monitoring) with the rights it needs."
    Action      = $action
    Principal   = $principal
    Settings    = $settings
    Force       = $true
}
if ($AutoStart) {
    $task.Trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
}
Register-ScheduledTask @task | Out-Null
Write-Output "Task 'LanWatch' registered for $user (start at sign-in: $($AutoStart.IsPresent))."
