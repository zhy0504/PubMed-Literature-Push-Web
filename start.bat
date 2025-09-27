@echo off
REM ===================================================================
REM PubMed Literature Push Web Application - Windows Startup Script
REM English version to avoid encoding issues
REM ===================================================================

REM Set code page to UTF-8 to handle any special characters
chcp 65001 >nul 2>&1

echo.
echo ===================================================
echo   PubMed Literature Push Web Application
echo ===================================================
echo.

REM Step 1: Check Python installation
echo [Step 1/5] Checking Python installation...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH
    echo.
    echo Please install Python 3.8 or higher:
    echo 1. Visit https://python.org/downloads
    echo 2. Download Python for Windows
    echo 3. During installation, check "Add Python to PATH"
    echo 4. Restart command prompt after installation
    echo.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version 2^>^&1') do set PYTHON_VERSION=%%i
echo [OK] Found %PYTHON_VERSION%
echo.

REM Step 2: Verify project files
echo [Step 2/5] Verifying project files...
if not exist "app.py" (
    echo [ERROR] app.py not found
    echo Please ensure you are running this script from the project directory
    echo Current directory: %CD%
    echo.
    pause
    exit /b 1
)
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt not found
    echo Project files may be incomplete
    echo.
    pause
    exit /b 1
)
echo [OK] Project files found
echo.

REM Step 3: Setup virtual environment
echo [Step 3/5] Setting up virtual environment...
if not exist "quick_venv" (
    echo Creating new virtual environment...
    python -m venv quick_venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment
        echo This may be due to:
        echo 1. Insufficient disk space
        echo 2. Permission issues
        echo 3. Python installation problems
        echo.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created
) else (
    echo [OK] Virtual environment already exists
)

REM Activate virtual environment
echo Activating virtual environment...
if not exist "quick_venv\Scripts\activate.bat" (
    echo [ERROR] Virtual environment appears corrupted
    echo Try deleting the quick_venv folder and run this script again
    echo.
    pause
    exit /b 1
)

call quick_venv\Scripts\activate.bat
echo [OK] Virtual environment activated
echo.

REM Step 4: Install dependencies (Fixed Logic)
echo [Step 4/5] Installing dependencies...

REM Check if core packages are available
python -c "import flask, requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing required packages...
    echo This may take a few minutes...
    echo.
    
    REM Show detailed installation process
    echo [DEBUG] Checking current pip version...
    python -m pip --version
    echo.
    
    echo [DEBUG] Ensuring pip is up to date...
    python -m pip install --upgrade pip --quiet
    if %errorlevel% neq 0 (
        echo [WARNING] Failed to upgrade pip, continuing with current version...
    ) else (
        echo [INFO] Pip check completed
    )
    echo.
    
    REM Check which requirements file to use and install
    echo [DEBUG] Checking available requirements files...
    if exist "requirements-compatible.txt" (
        echo [INFO] Found requirements-compatible.txt - using compatible versions
        echo [DEBUG] Installing dependencies from requirements-compatible.txt...
        echo [DEBUG] Full command: pip install -r requirements-compatible.txt --verbose
        echo.
        pip install -r requirements-compatible.txt --verbose
        set "INSTALL_SUCCESS=1"
    ) else if exist "requirements-minimal.txt" (
        echo [INFO] Found requirements-minimal.txt - using minimal versions
        echo [DEBUG] Installing dependencies from requirements-minimal.txt...
        echo [DEBUG] Full command: pip install -r requirements-minimal.txt --verbose
        echo.
        pip install -r requirements-minimal.txt --verbose
        set "INSTALL_SUCCESS=1"
    ) else (
        echo [INFO] Using standard requirements.txt
        echo [DEBUG] Installing dependencies from requirements.txt...
        echo [DEBUG] Full command: pip install -r requirements.txt --verbose
        echo.
        pip install -r requirements.txt --verbose
        set "INSTALL_SUCCESS=1"
    )
    
    echo.
    echo [OK] Package installation completed
) else (
    echo [OK] Dependencies already installed
    set "INSTALL_SUCCESS=1"
)

REM Final verification by testing imports
echo [DEBUG] Verifying installation by testing imports...
python -c "import flask, requests; print('Core packages: OK')" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Core packages verification failed
    echo Please run the installation manually or check for errors
    pause
    exit /b 1
) else (
    echo [INFO] Core packages verified successfully
)

REM Test optional packages
python -c "import openai; print('OpenAI: Available')" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] OpenAI not available - AI features will be disabled
) else (
    echo [INFO] OpenAI available - AI features enabled
)
echo.

REM Step 5: Initialize database
echo [Step 5/5] Checking database...
if not exist "pubmed_app.db" (
    echo Initializing database...
    echo.
    echo Choose setup method:
    echo   1. Interactive setup with custom accounts
    echo   2. Quick setup with default accounts
    echo.
    
    choice /c 12 /m "Choose setup method"
    if errorlevel 2 (
        echo Running quick default setup...
        python setup.py --default
    ) else (
        echo Running interactive setup...
        python setup.py
    )
    
    if %errorlevel% neq 0 (
        echo [ERROR] Database setup failed
        echo.
        pause
        exit /b 1
    )
    echo [OK] Database setup completed
) else (
    echo [OK] Database already exists
)
echo.

REM All checks passed - start the application
echo ===================================================
echo   All checks passed! Starting application...
echo ===================================================
echo.
echo Choose debug mode:
echo   1. Development mode (debug=True, auto-reload)
echo   2. Production mode (debug=False, stable)
echo.

choice /c 12 /m "Choose mode"
if errorlevel 2 (
    echo Setting production mode...
    set FLASK_DEBUG=False
) else (
    echo Setting development mode...
    set FLASK_DEBUG=True
)
echo.
echo Press Ctrl+C to stop the server
echo.
echo Starting in 3 seconds...
timeout /t 3 /nobreak >nul

REM Start the Flask application
python app.py

REM Check if application exited with error
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Application stopped unexpectedly
    echo Error code: %errorlevel%
    echo.
    echo Common issues and solutions:
    echo 1. Port 5003 already in use:
    echo    - Run: netstat -ano ^| findstr :5003
    echo    - Kill the process or change port in config.py
    echo.
    echo 2. Configuration errors:
    echo    - Check config.py file
    echo    - Verify email settings if using mail features
    echo.
    echo 3. Missing files:
    echo    - Ensure all project files are present
    echo    - Re-download if necessary
    echo.
    pause
    exit /b 1
)

echo.
echo [INFO] Application stopped normally
echo Thank you for using PubMed Literature Push!
pause
exit /b 0