@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3.12 is not installed. Get it from python.org, tick "Add to PATH", then run this again. & pause & exit /b 1)
echo Installing connector libraries...
python -m pip install -q -r requirements-connector.txt || (echo pip failed & pause & exit /b 1)
echo.
echo Make sure TallyPrime is open with a company loaded and the HTTP/XML server on port 9000.
echo.
set /p CO=Company name exactly as in Tally (press Enter to skip): 
if "%CO%"=="" (python -m dueclock.connector tally-check --save-profile) else (python -m dueclock.connector tally-check --save-profile --company "%CO%")
echo.
echo Send the folder named in the last line above, plus a photo or copy of this window.
pause
