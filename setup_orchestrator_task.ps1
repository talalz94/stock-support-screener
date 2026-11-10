# Registers the nightly orchestrator as the Windows scheduled task "Screener-Orchestrator",
# configured the way it runs on the original machine (read from the live task, 2026-11-11):
#
#   - daily at 05:00 local time, then every 2 hours for 12 hours (05:00 .. 17:00), so a run cut
#     short by sleep or a restart resumes within two hours;
#   - a start missed while the machine was off runs as soon as it is available;
#   - one instance at a time; below-normal priority (7); a 12-hour limit;
#   - if it fails to start, 3 more tries 20 minutes apart;
#   - runs on battery, does not wake the machine, and runs only while you are logged on.
#
#   Install:  powershell -ExecutionPolicy Bypass -File setup_orchestrator_task.ps1
#   Check:    powershell -ExecutionPolicy Bypass -File setup_orchestrator_task.ps1 -DryRun
#   Remove:   powershell -ExecutionPolicy Bypass -File setup_orchestrator_task.ps1 -Remove
#
# 05:00 suits Pakistan time, where the US close lands around 01:00-02:00. Elsewhere pass -At with a
# time a few hours after 16:00 New York time. -Python points at pythonw.exe if it is not next to the
# python.exe on PATH. (setup_schedule.ps1 is the older 03:00 daily_run task, now a no-op.)
param([switch]$Remove, [switch]$DryRun, [string]$At = "05:00", [string]$Python = "")

$ErrorActionPreference = "Stop"
$Name = "Screener-Orchestrator"
$Dir = $PSScriptRoot

if ($Remove) {
    if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $Name -Confirm:$false
        Write-Host "Removed $Name."
    } else {
        Write-Host "$Name is not registered."
    }
    return
}

if (-not $Python) {
    # pythonw.exe runs without a console window; it sits next to python.exe.
    $py = (Get-Command python.exe -ErrorAction Stop).Source
    $Python = Join-Path (Split-Path $py) "pythonw.exe"
}
if (-not (Test-Path $Python)) { throw "pythonw.exe not found at $Python; pass -Python <path to pythonw.exe>" }
if (-not (Test-Path (Join-Path $Dir "orchestrator.py"))) { throw "orchestrator.py is not next to this script ($Dir)" }

$action = New-ScheduledTaskAction -Execute $Python -Argument "orchestrator.py --wait-for-lock 300" -WorkingDirectory $Dir
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# A daily trigger takes its repetition from a one-off trigger built with one: the usual way in PowerShell.
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At $At -RepetitionInterval (New-TimeSpan -Hours 2) `
                       -RepetitionDuration (New-TimeSpan -Hours 12)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 12) -Priority 7 -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 20)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Support Bounce Screener: the nightly orchestrator (registered by setup_orchestrator_task.ps1)"

if ($DryRun) {
    Write-Host "Would register $Name for $($env:USERNAME), changing nothing now:"
    Write-Host "  run       $Python orchestrator.py --wait-for-lock 300"
    Write-Host "  in        $Dir"
    Write-Host "  when      daily at $At, then every $($trigger.Repetition.Interval) for $($trigger.Repetition.Duration); a missed start runs when available"
    Write-Host "  settings  priority $($settings.Priority), $($settings.MultipleInstances), limit $($settings.ExecutionTimeLimit), $($settings.RestartCount) restarts $($settings.RestartInterval) apart"
    return
}

Register-ScheduledTask -TaskName $Name -InputObject $task -Force | Out-Null
Write-Host "Registered $Name. Next run: $((Get-ScheduledTaskInfo -TaskName $Name).NextRunTime)"
