@echo off
echo === POKER ADVISOR ===

REM Kill old advisor
taskkill /F /IM python.exe >nul 2>&1

REM Check if Chrome already running with debug port
curl -s http://127.0.0.1:9222/json/version >nul 2>&1
if %ERRORLEVEL%==0 (
    echo Chrome already running.
    goto advisor
)

REM Kill any Chrome without debug port
taskkill /IM chrome.exe >nul 2>&1
timeout /t 2 /nobreak >nul
taskkill /F /IM chrome.exe >nul 2>&1

:waitdead
tasklist /FI "IMAGENAME eq chrome.exe" 2>NUL | find /I "chrome.exe" >NUL
if %ERRORLEVEL%==0 (
    timeout /t 1 /nobreak >nul
    goto waitdead
)

REM Fix crash flag
if exist "C:\Users\Simon\chrome-debug\Default\Preferences" (
    C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe -c "import json;p=r'C:\Users\Simon\chrome-debug\Default\Preferences';d=json.load(open(p));d.setdefault('profile',{})['exited_cleanly']=True;d['profile']['exit_type']='Normal';json.dump(d,open(p,'w'))" >nul 2>&1
)

REM Launch Chrome with debug port
echo Starting Chrome...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir=C:\Users\Simon\chrome-debug ^
  --no-first-run ^
  --disable-blink-features=AutomationControlled ^
  --disable-features=PasswordManagerOnboarding,IsolateOrigins,site-per-process ^
  --exclude-switches=enable-automation ^
  --window-size=1280,721 ^
  --window-position=0,0 ^
  "about:blank"

:portloop
timeout /t 1 /nobreak >nul
curl -s http://127.0.0.1:9222/json/version >nul 2>&1
if %ERRORLEVEL% NEQ 0 goto portloop
echo Chrome ready.

REM Login if needed
timeout /t 5 /nobreak >nul
cd /d C:\poker-research
node scripts/auto-login.js

REM Click CASH GAME via OCR
timeout /t 3 /nobreak >nul
echo Clicking Cash Game...
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe scripts/ocr-click.py "CASH GAME"

:advisor
echo Starting advisor...
cd /d C:\poker-research
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe -u vision/advisor_ws.py
