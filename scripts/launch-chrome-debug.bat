@echo off
REM Launch Chrome with remote debugging enabled for CDP capture
REM Uses a separate profile so your main Chrome stays untouched

set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
set PORT=9222
set PROFILE="C:\chrome-poker-profile"

echo Starting Chrome with remote debugging on port %PORT%...
echo Profile: %PROFILE%
echo.
echo You can now run:  node scripts\cdp-capture.js
echo.

start "" %CHROME% --remote-debugging-port=%PORT% --user-data-dir=%PROFILE%
