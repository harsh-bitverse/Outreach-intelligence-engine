@echo off
chcp 65001 >nul
title Opportunity Intelligence Engine -- Unified Scheduler
color 0A
cd /d "C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine"
echo ==============================================================================
echo   OPPORTUNITY INTELLIGENCE ENGINE -- UNIFIED SCHEDULER LAUNCHER
echo ==============================================================================
echo   Working Directory: C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine
echo   Python: C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine\.venv\Scripts\python.exe
echo   Starting unified scheduler with CLI authoring and live SMTP enabled...
echo.
"C:\Users\hagrh\OneDrive\Desktop\Projects\Outreach Intelligence Engine\.venv\Scripts\python.exe" "scripts\run_unified_scheduler.py" --cli --continuous --live
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Unified Scheduler process exited with code %ERRORLEVEL%.
    echo Check logs\unified_campaign_execution.log for diagnostic trace.
    pause
)
pause
