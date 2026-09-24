@echo off
"%~dp0dependencies\python\python.exe" "%~dp0components\montage-three-part-orchestrator-ff\scripts\three_suite_ff.py" %*
exit /b %errorlevel%
