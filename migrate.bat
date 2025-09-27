@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM 切换到脚本所在目录
cd /d "%~dp0"

set FLASK_APP=app.py

if "%~1"=="help" (
    echo Flask-Migrate 使用帮助:
    echo migrate.bat migrate "描述" - 创建新迁移
    echo migrate.bat upgrade        - 应用迁移
    echo migrate.bat history        - 查看历史
    echo migrate.bat current        - 查看当前版本
    echo migrate.bat downgrade      - 回滚到上一版本
) else if "%~1"=="migrate" (
    if "%~2"=="" (
        echo 错误: 请提供迁移描述
        echo 用法: migrate.bat migrate "你的描述"
    ) else (
        echo 创建迁移: %~2
        quick_venv\Scripts\python.exe -c "import os; os.environ['FLASK_APP'] = 'app.py'; from flask.cli import main; import sys; sys.argv = ['flask', 'db', 'migrate', '-m', '%~2']; main()"
    )
) else if "%~1"=="upgrade" (
    echo 应用迁移...
    quick_venv\Scripts\python.exe -c "import os; os.environ['FLASK_APP'] = 'app.py'; from flask.cli import main; import sys; sys.argv = ['flask', 'db', 'upgrade']; main()"
) else if "%~1"=="history" (
    echo 迁移历史:
    quick_venv\Scripts\python.exe -c "import os; os.environ['FLASK_APP'] = 'app.py'; from flask.cli import main; import sys; sys.argv = ['flask', 'db', 'history']; main()"
) else if "%~1"=="current" (
    echo 当前版本:
    quick_venv\Scripts\python.exe -c "import os; os.environ['FLASK_APP'] = 'app.py'; from flask.cli import main; import sys; sys.argv = ['flask', 'db', 'current']; main()"
) else if "%~1"=="downgrade" (
    echo 回滚到上一版本...
    quick_venv\Scripts\python.exe -c "import os; os.environ['FLASK_APP'] = 'app.py'; from flask.cli import main; import sys; sys.argv = ['flask', 'db', 'downgrade']; main()"
) else (
    echo 用法: migrate.bat [migrate^|upgrade^|history^|current^|downgrade^|help]
    echo 运行 'migrate.bat help' 查看详细帮助
)