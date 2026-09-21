@echo off
set "LOG=%LOCALAPPDATA%\YOYOClawCheckin\checkin.log"
if not exist "%LOG%" (
  echo 还没有日志。先双击 立即签到.bat 跑一次。
  pause
  exit /b 0
)
start "" notepad "%LOG%"
