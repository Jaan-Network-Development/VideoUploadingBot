# Optional: run the uploader at logon and restart it if it fails (Windows equivalent of the systemd unit).
# It must run in a logged-in desktop session (it drives a real Chrome window), hence AtLogOn.
# Run in PowerShell:  powershell -ExecutionPolicy Bypass -File deploy\windows\install-task.ps1
$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$action = New-ScheduledTaskAction -Execute "$root\run.bat" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "VideoUploaderBot" -Action $action -Trigger $trigger -Settings $settings -Force
Write-Host "Registered. Start now with: Start-ScheduledTask -TaskName VideoUploaderBot"
