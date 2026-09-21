@echo off
cd /d "%~dp0"
title YOYO Claw 自动签到 - 一键配置
if not exist "python\python.exe" goto nopy
if not exist "YOYOClawCheckin.py" goto nopy

echo ========================================================
echo   YOYO Claw 每日自动签到 - 一键配置
echo ========================================================
echo.
echo  将注册一个 Windows 计划任务，之后你什么都不用管：
echo    - 每次登录系统时自动检查一次
echo    - 每天 12:00 和 20:00 各再检查一次
echo    - 错过时间点也没关系，电脑空下来会自动补跑
echo.
echo  正在配置，约 5 到 20 秒，请不要关闭这个窗口 ...
echo.
"python\python.exe" "YOYOClawCheckin.py" --install-task
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo [成功] 配置完成，并且已经当场验证过一次。
) else (
  echo [注意] 没有完全成功，退出码 %RC%，请看上面的提示。
)
echo.
echo 想看它每天跑成什么样：双击  查看运行日志.bat
echo 想取消自动签到：      双击  一键解除配置.bat
echo.
pause
exit /b 0

:nopy
echo.
echo [错误] 目录不完整，缺少 python\python.exe 或 YOYOClawCheckin.py
echo        请重新完整解压压缩包，不要只把 bat 单独拷出来。
echo.
pause
