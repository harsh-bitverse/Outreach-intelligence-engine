# Register Opportunity Engine Recurring Discovery Task in Windows Task Scheduler

$TaskName = "OpportunityEngine_Recurring_Discovery"
$PythonExe = "C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine\.venv\Scripts\python.exe"
$ScriptPath = "scripts\run_recurring_discovery_worker.py"
$WorkingDir = "C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine"
$Arguments = "$ScriptPath --once"

# Create action
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments -WorkingDirectory $WorkingDir

# Create trigger (Daily, repeating every 1 hour)
$Trigger = New-ScheduledTaskTrigger -Daily -At "07:00AM"

# Create settings
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Register or update task
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Opportunity Intelligence Engine Autonomous Recurring Discovery"
    Write-Output "[SUCCESS] Task '$TaskName' registered successfully."
} catch {
    Write-Output "[ERROR] Failed to register task: $_"
}
