@echo off
"%~dp0runtime\python\python.exe" "%~dp0components\montage-three-part-orchestrator-ff\scripts\three_suite_ff.py" %*
exit /b %errorlevel%
