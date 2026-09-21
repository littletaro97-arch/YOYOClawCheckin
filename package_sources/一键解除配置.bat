@echo off
cd /d "%~dp0"
title YOYO Claw 自动签到 - 一键解除配置
if not exist "python\python.exe" goto nopy

echo ========================================================
echo   YOYO Claw 每日自动签到 - 一键解除配置
echo ========================================================
echo.
echo  将删除已注册的计划任务，之后不再自动签到。
echo.
"python\python.exe" "YOYOClawCheckin.py" --uninstall-task
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo [成功] 已解除配置。
) else (
  echo [注意] 解除配置没成功，退出码 %RC%，请看上面的提示。
)
echo.
echo 说明：本程序不改动 YOYO Claw 本身，也不动你的账号数据。
echo       签到记录留在下面这个文件夹，不需要的话整个删掉就彻底干净了：
echo         %LOCALAPPDATA%\YOYOClawCheckin
echo       程序文件夹本身也可以直接删除。
echo.
pause
exit /b 0

:nopy
echo.
echo [错误] 缺少 python\python.exe，请重新完整解压压缩包。
echo.
pause
