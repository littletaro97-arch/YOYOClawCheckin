@echo off
cd /d "%~dp0"
if not exist "python\python.exe" goto nopy
if not exist "YOYOClawCheckin.py" goto nopy

"python\python.exe" "YOYOClawCheckin.py"
goto done

:nopy
echo.
echo [错误] 目录不完整，缺少 python\python.exe 或 YOYOClawCheckin.py
echo        请重新完整解压压缩包，不要只复制单个文件出来。
echo.

:done
echo.
echo --------------------------------------------------------
echo 退出码说明:
echo   0  成功（或今天已经签到过）
echo   2  等不到登录态刷新
echo   3  查询签到状态失败
echo   4  领取失败
echo   5  领取成功但复核失败
echo   6  本机没找到 YOYO Claw
echo   7  没有登录荣耀账号
echo --------------------------------------------------------
echo 出现 2/3/4/5 时程序会自动重试，间隔 15 秒，最多再试 2 次。
echo 出现 6/7 不会重试（重试也没用），直接结束。
echo 这段时间内窗口会停在这里，属于正常现象，等它自己结束。
pause
