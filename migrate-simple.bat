@echo off
chcp 65001 >nul
cd /d "%~dp0"

:start
cls
echo ==========================================
echo        Flask-Migrate Management Tool
echo ==========================================
echo.
echo Please select an option:
echo.
echo [1] Show current version
echo [2] Show migration history  
echo [3] Apply migrations
echo [4] Create new migration
echo [5] Rollback migration
echo [6] Exit
echo.
echo ==========================================

choice /c 123456 /n /m "Enter option (1-6): "

if errorlevel 6 goto exit
if errorlevel 5 goto downgrade
if errorlevel 4 goto new_migration
if errorlevel 3 goto upgrade
if errorlevel 2 goto history
if errorlevel 1 goto current

:current
cls
echo Checking current version...
echo.
call migrate.bat current
echo.
pause
goto start

:history
cls
echo Showing migration history...
echo.
call migrate.bat history
echo.
pause
goto start

:upgrade
cls
echo Applying migrations...
echo.
call migrate.bat upgrade
echo.
pause
goto start

:new_migration
cls
set /p desc=Enter migration description: 
echo.
echo Creating migration: %desc%
call migrate.bat migrate "%desc%"
echo.
pause
goto start

:downgrade
cls
echo WARNING: About to rollback to previous version!
choice /c yn /n /m "Continue? (Y/N): "
if errorlevel 2 goto start
echo.
echo Rolling back...
call migrate.bat downgrade
echo.
pause
goto start

:exit
cls
echo Thank you for using Flask-Migrate tool!
timeout /t 2 /nobreak >nul