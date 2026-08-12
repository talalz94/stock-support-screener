# Registers the daily support-bounce screen as a Windows Scheduled Task.
#
#   Install:  powershell -ExecutionPolicy Bypass -File setup_schedule.ps1
#   Remove:   powershell -ExecutionPolicy Bypass -File setup_schedule.ps1 -Remove
#
# Adapted from Stock Screener\setup_schedule.ps1, which is proven live on this
# machine (SP500-DailyUpdate, LastTaskResult 0). Differences, all deliberate:
#
#   03:00 not 02:00   02:00 is taken by SP500-DailyUpdate. 03:00 PKT is 17:00 ET
#                     the previous day -- an hour after the close, well clear of
#                     the free-tier SIP 15-minute embargo.
#   AtLogOn +PT7M     so the two tasks do not stampede the same network on login.
#   1h time limit     the daily path is ~60-90s, so anything near an hour is a
#                     hang and should be killed rather than left running.
#
# Built for a laptop that is often closed:
#   TRIGGER 1  daily 03:00       - the normal after-close run
#   TRIGGER 2  at logon (+7min)  - catches up whenever the machine comes back
#   StartWhenAvailable           - also runs a missed 03:00 once available
#
# daily_run.py is idempotent: if the bars are current and the split-recheck is
# already done for the session it exits in a couple of seconds, so the extra
# logon trigger costs nothing on days you were already online. It also
# reconciles any sessions missed while the machine was off, so days-on-list
# stays honest after a gap.
#
# To stop it doing work without unregistering:  python daily_run.py --pause

param(
    [switch]$Remove,
    # Minutes between sentiment passes. 0 disables that task entirely.
    # Free-tier news carries a ~15-minute delay, so ~15 is the practical floor.
    [int]$SentimentInterval = 30,
    # Local clock time corresponding to the US open. senti_screen re-checks
    # SENTI_HOURS in ET itself, so a wrong value here costs a no-op run.
    [string]$SentiStart = "18:30"
)

$ErrorActionPreference = "Stop"

$TaskName  = "PatternScan-DailyRun"
$SentiTask = "PatternScan-Sentiment"
$ScriptDir = $PSScriptRoot
$RunAt     = "03:00"
$Window    = 5

# Registering a task in the Task Scheduler root needs elevation. If we are not
# elevated, relaunch this same script via UAC and let the elevated copy do it.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin  = ([Security.Principal.WindowsPrincipal]$identity).IsInRole(
                [Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "Administrator rights are required to register a scheduled task."
    Write-Host "Relaunching with elevation - approve the UAC prompt..." -ForegroundColor Yellow
    $argList = @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"")
    if ($Remove) { $argList += "-Remove" }
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argList
    } catch {
        Write-Error "Elevation was declined. Right-click PowerShell > 'Run as administrator', then re-run."
    }
    return
}

if ($Remove) {
    foreach ($n in @($TaskName, $SentiTask)) {
        if (Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $n -Confirm:$false
            Write-Host "Unregistered '$n'." -ForegroundColor Green
        } else {
            Write-Host "'$n' is not registered."
        }
    }
    return
}

# pythonw.exe runs without a console window; output still goes to data\_run.log
$PyExe = (Get-Command python).Source
$PyW   = Join-Path (Split-Path $PyExe) "pythonw.exe"
if (-not (Test-Path $PyW)) {
    Write-Warning "pythonw.exe not found next to python.exe; falling back to python.exe (a console window will appear)."
    $PyW = $PyExe
}

Write-Host "Registering '$TaskName'"
Write-Host "  python : $PyW"
Write-Host "  folder : $ScriptDir"
Write-Host "  daily  : $RunAt local (= 17:00 ET previous day), plus at every logon (+7 min)"
Write-Host "  window : $Window sessions kept gap-free"

$action = New-ScheduledTaskAction -Execute $PyW `
    -Argument "daily_run.py --window $Window" -WorkingDirectory $ScriptDir

$daily = New-ScheduledTaskTrigger -Daily -At $RunAt
$logon = New-ScheduledTaskTrigger -AtLogOn
$logon.Delay = "PT7M"     # let the machine settle, and stay clear of the 02:00 task

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 20) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

# We are running elevated, but the TASK must run as the normal interactive user
# at Limited level -- otherwise it would demand elevation at 03:00 and silently
# fail. This pins it to the account that owns the data folder.
$owner = $identity.Name
Write-Host "  runs as: $owner (interactive, non-elevated)"

$principal = New-ScheduledTaskPrincipal -UserId $owner `
    -LogonType Interactive -RunLevel Limited

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "  (replaced existing task)"
}

Register-ScheduledTask -TaskName $TaskName -Action $action `
    -Trigger @($daily, $logon) -Settings $settings -Principal $principal `
    -Description "Daily US support-bounce screen: parabolic run -> full retrace to base -> base holds -> bounce. Writes reports\latest.html." | Out-Null

Write-Host ""
Write-Host "Registered." -ForegroundColor Green

# ---------------------------------------------------------------- sentiment
# A SECOND task, deliberately separate rather than more triggers on the first.
# The bounce run is one shot per session; the sentiment screener repeats through
# the session, and folding both into one task would mean either running the full
# bounce pipeline every 30 minutes or teaching it to skip itself.
#
# -SentimentInterval 0 skips this entirely.
if ($SentimentInterval -gt 0) {
    Write-Host ""
    Write-Host "Registering '$SentiTask' (every $SentimentInterval min, market hours)"

    $sAction = New-ScheduledTaskAction -Execute $PyW `
        -Argument "senti_screen.py" -WorkingDirectory $ScriptDir

    # Repetition across the regular session in ET. The start time is local, so
    # this is 09:30 ET expressed in local time by the same offset the 03:00 run
    # already assumes. senti_screen re-checks SENTI_HOURS itself and exits early
    # outside them, so a wrong offset costs a no-op, not bad data.
    $sTrigger = New-ScheduledTaskTrigger -Daily -At $SentiStart
    $sTrigger.Repetition = (New-ScheduledTaskTrigger -Once -At $SentiStart `
        -RepetitionInterval (New-TimeSpan -Minutes $SentimentInterval) `
        -RepetitionDuration (New-TimeSpan -Hours 7)).Repetition

    $sSettings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
        -MultipleInstances IgnoreNew

    if (Get-ScheduledTask -TaskName $SentiTask -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $SentiTask -Confirm:$false
        Write-Host "  (replaced existing task)"
    }

    Register-ScheduledTask -TaskName $SentiTask -Action $sAction `
        -Trigger $sTrigger -Settings $sSettings -Principal $principal `
        -Description "Sentiment screen: Alpaca news -> event taxonomy -> measured severity. Writes reports\sentiment_latest.html." | Out-Null

    Write-Host "  Registered." -ForegroundColor Green
}

Write-Host ""
Write-Host "  Run it now      : Start-ScheduledTask   -TaskName $TaskName"
Write-Host "  Check last run  : Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "  Remove          : powershell -ExecutionPolicy Bypass -File setup_schedule.ps1 -Remove"
Write-Host "  Pause work      : python daily_run.py --pause"
Write-Host "  Pause sentiment : python senti_screen.py --pause"
Write-Host "  Health          : python status.py"
Write-Host "  Today's report  : $ScriptDir\reports\latest.html"
Write-Host "  Sentiment       : $ScriptDir\reports\sentiment_latest.html"
