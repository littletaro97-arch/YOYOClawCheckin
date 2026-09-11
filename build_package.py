# -*- coding: utf-8 -*-
"""打包脚本：生成可分发的 Windows 压缩包。

用法：
    python build_package.py

产物：
    dist\\YOYOClawCheckin\\            解压后的目录
    dist\\YOYOClawCheckin-v1.2.zip    可直接分发的压缩包

打包需要官方 Windows 嵌入式 Python 3.12：
    https://www.python.org/downloads/windows/  (Windows embeddable package, 64-bit)
解压后把内容放到 dist\\YOYOClawCheckin\\python\\ 即可，脚本会自己检查。
"""
import os
import shutil
import zipfile

VERSION = "1.2"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "YOYOClawCheckin.py")
DIST = os.path.join(HERE, "dist")
DST = os.path.join(DIST, "YOYOClawCheckin")
ZIP = os.path.join(DIST, "YOYOClawCheckin-v%s.zip" % VERSION)

BATS = {
    "立即签到.bat": r"""@echo off
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
""",
    "安装到开机启动.bat": r"""@echo off
cd /d "%~dp0"
if not exist "python\python.exe" goto nopy

"python\python.exe" "YOYOClawCheckin.py" --install-startup
echo.
echo 已写入 Windows 启动目录，下次登录系统时会自动静默运行。
echo 想撤销就双击 取消开机启动.bat
echo.
pause
exit /b 0

:nopy
echo [错误] 缺少 python\python.exe，请重新完整解压。
pause
""",
    "取消开机启动.bat": r"""@echo off
cd /d "%~dp0"
if not exist "python\python.exe" goto nopy

"python\python.exe" "YOYOClawCheckin.py" --remove-startup
echo.
echo 已取消开机启动。程序文件夹可以整个删掉。
echo.
pause
exit /b 0

:nopy
echo [错误] 缺少 python\python.exe。
pause
""",
    "查看运行日志.bat": r"""@echo off
set "LOG=%LOCALAPPDATA%\YOYOClawCheckin\checkin.log"
if not exist "%LOG%" (
  echo 还没有日志。先双击 立即签到.bat 跑一次。
  pause
  exit /b 0
)
start "" notepad "%LOG%"
""",
}

USAGE = """YOYO Claw 每日自动签到  v{ver}
=====================================

v1.2 变更：
  * 没登录荣耀账号时不再干等，直接报"未登录"并给出处理办法（约 1 秒内结束）
  * 登录态被服务端拒绝（401）也按"未登录"处理，不再当成网络错误反复重试
  * 开机启动那次是静默的，只有彻底失败才会弹一次窗提醒

v1.1 变更：失败后自动立即重试（间隔 15 秒，最多再试 2 次）。

【它做什么】
  开机登录后，在后台检查你今天的 YOYO Claw 积分签到有没有领；没领就领一次。
  只调客户端自己调的那个接口，一天最多领一次，领过就跳过，不会重复刷。
  全程不联网上传任何东西，不收集账号密码。

  失败会自动重试：间隔 15 秒，最多再试 2 次（首次 + 2 次重试共 3 次）。
  "本机没找到 YOYO Claw" 不会重试，因为再试也没用。

【前提】
  1. 电脑上装了荣耀 YOYO Claw（MagicClaw），并且已经登录荣耀账号。
  2. Windows 10 / 11，64 位。
  3. 不需要安装 Python，包里自带了一个精简版 Python。

【怎么用】
  1. 把整个文件夹解压到任意位置（建议放个不动的地方，比如 D:\\Tools\\YOYOClawCheckin）。
     注意：解压后不要只把 bat 拷出来，程序需要 python 子目录。
  2. 双击 "立即签到.bat" 先手动跑一次，确认窗口里出现"成功"或"今天已经签到过"。
  3. 确认没问题后，双击 "安装到开机启动.bat"，以后就不用管了。
  4. 想看看每天跑成什么样，双击 "查看运行日志.bat"。

【有没有界面】
  开机自动那次**完全静默**，没有窗口、没有托盘图标，你不会感觉到它跑过。
  只有一种情况会弹窗：重试 2 次后仍然失败，才会弹一次提示告诉你原因。
  成功、或者"今天已经签到过"，都不会弹任何东西——不打扰是默认行为。
  想确认跑没跑，双击 "查看运行日志.bat"。

【退出码】
  0 成功 / 今天已签到    2 等不到登录态刷新
  3 查询失败             4 领取失败
  5 领取了但复核失败      6 没找到 YOYO Claw
  7 没有登录荣耀账号
  2/3/4/5 会自动重试；6/7 不重试（重试也没用）。

【常见问题】
  Q: 报"没找到 YOYO Claw"？
  A: 确认装了客户端。如果装在奇怪的位置，设一个系统环境变量
     YOYOCLAW_ROOT 指向安装根目录（包含 current.json 的那一层）。

  Q: 报"没有登录荣耀账号"（退出码 7）？
  A: 打开 YOYO Claw，确认右上角是已登录状态，登录一次之后就全自动了。
     这个错误不会自动重试，因为再试也没用——必须你手动登录一次。

  Q: 报"等不到登录态刷新"（退出码 2）？
  A: 客户端在刷新但没赶上，会自动重试 2 次。还不行就手动重跑一次。

  Q: 杀毒软件报警？
  A: 程序需要解密本机 YOYO Claw 的登录态（Windows DPAPI），
     这一行为和浏览器读取自己的 Cookie 是同一类操作，
     个别杀软会按特征误报。源码 YOYOClawCheckin.py 是明文，可以自己看。

  Q: 当天开机那次失败了，还会自动补吗？
  A: 会先自动重试 2 次（间隔 15 秒）。三次都失败就放弃，
     但只有真正成功才会记录"今天已签"，所以你随时双击"立即签到.bat"
     都能重新跑，一天内都还来得及。

【卸载】
  双击 "取消开机启动.bat"，然后把整个文件夹删掉即可。
  记录文件在 %LOCALAPPDATA%\\YOYOClawCheckin\\ ，一起删掉就干净了。

【注意】
  仅供你自己在自己的电脑上、用自己的账号使用。
  不要拿去给别人代签，也不要改成批量请求。
""".format(ver=VERSION)


def main():
    if not os.path.isfile(SRC):
        raise SystemExit("找不到主程序: %s" % SRC)

    os.makedirs(DST, exist_ok=True)
    shutil.copy2(SRC, os.path.join(DST, "YOYOClawCheckin.py"))

    for name, body in BATS.items():
        # cmd.exe 按系统 ANSI 码页读 bat，中文必须写 GBK
        with open(os.path.join(DST, name), "w", encoding="gbk", newline="\r\n") as f:
            f.write(body)

    with open(os.path.join(DST, "使用说明.txt"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(USAGE)

    pyexe = os.path.join(DST, "python", "python.exe")
    if not os.path.isfile(pyexe):
        print("[跳过] 没找到 %s，未生成 zip。" % pyexe)
        print("       去 https://www.python.org/downloads/windows/ 下")
        print("       \"Windows embeddable package (64-bit)\"，解压到上面那个 python\\ 目录，")
        print("       然后重新运行本脚本。")
        return

    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, _, files in os.walk(DST):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, DIST))
    print("[完成] %s (%.1f MB)" % (ZIP, os.path.getsize(ZIP) / 1048576))


if __name__ == "__main__":
    main()
