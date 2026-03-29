@echo off
REM Start CDP capture session
REM Press Ctrl+C to stop and save

cd /d "%~dp0.."
echo.
echo === CDP Capture ===
echo Output: captures\[timestamp]\
echo Press Ctrl+C to stop capture
echo.
node scripts\cdp-capture.js
