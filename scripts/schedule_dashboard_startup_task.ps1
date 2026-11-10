# Register Opportunity Intelligence Engine Operations Dashboard Task in Windows Task Scheduler
# Optional startup task: Launches the dashboard server on user logon

$TaskName = "OpportunityEngine_Observability_Dashboard"
$PythonExe = "C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine\.venv\Scripts\python.exe"
$ScriptPath = "scripts\run_dashboard_server.py"
$WorkingDir = "C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine"
$Arguments = "$ScriptPath --host 127.0.0.1 --port 8000"

# Create action
$Action = New-ScheduledTaskAction -Execute $PythonExe -Argument $Arguments -WorkingDirectory $WorkingDir

# Create trigger (At Logon)
$Trigger = New-ScheduledTaskTrigger -AtLogOn

# Create settings
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 365)

# Register task
try {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Description "Opportunity Intelligence Engine Real-Time Operations Observability Dashboard"
    Write-Output "[SUCCESS] Task '$TaskName' registered successfully."
} catch {
    Write-Output "[ERROR] Failed to register task: $_"
}
