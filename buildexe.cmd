@echo off
chcp 65001 >nul
setlocal

set "SRC_DIR=%~dp0"
set "ENTRY=%SRC_DIR%index.py"
set "DIST_DIR=%SRC_DIR%dist"
set "APP_NAME=VideoAssetManager"

echo ========================================
echo  正在打包 %APP_NAME% ...
echo ========================================

pyinstaller --noconsole --onefile --name "视频素材助手" --clean index.py
if %errorlevel% neq 0 (
    echo.
    echo [错误] PyInstaller 打包失败，请检查环境是否已安装 pyinstaller。
    echo        pip install pyinstaller
    pause
    exit /b 1
)

echo.
echo 正在复制 Everything.exe 到输出目录...
copy /y "%SRC_DIR%everything.exe" "%DIST_DIR%\Everything.exe"

if %errorlevel% neq 0 (
    echo [警告] 复制 Everything.exe 失败，请手动复制。
) else (
    echo Everything.exe 已复制到 %DIST_DIR%\
)

echo.
echo ========================================
echo  打包完成！输出目录: %DIST_DIR%
echo  - %APP_NAME%.exe
echo  - Everything.exe
echo ========================================
pause
