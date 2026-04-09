@echo off
REM Unibet end-to-end session — launches the orchestrator that handles
REM Chrome, login, table navigation, play, re-buy, breaks, and cleanup.
REM
REM Use this INSTEAD of start.bat when you want the full automated flow.
REM start.bat only launches the overlay-only advisor (you click manually).

cd /d C:\poker-research

REM Defaults: 3 rebuys, no hand cap (use SessionManager's natural limit),
REM don't wait for BB (post the dead BB; we'd lose an orbit otherwise).
C:\Users\Simon\AppData\Local\Programs\Python\Python312\python.exe -u vision\unibet_session_runner.py %*
