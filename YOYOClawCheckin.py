# -*- coding: utf-8 -*-
"""
YOYO Claw 每日自动签到（纯标准库实现）

流程：
  1. 读 state.json：今天已成功则退出
  2. 确保 YOYO Claw 已运行（未运行则启动）
  3. 等待客户端刷新本地会话（session.bin 更新且 access token 未过期）
  4. 解密 session.bin 取得 access_token / device_fingerprint
  5. GET  签到日历 -> 判断今天是否已签到
  6. 未签到则 POST 签到（仅一次）
  7. 再次 GET 校验
  8. 写 state.json

用法：
  python YOYOClawCheckin.py              # 正常执行
  python YOYOClawCheckin.py --dry-run    # 只查询，不签到
  python YOYOClawCheckin.py --install-task     # 一键配置（注册计划任务 + 立即验证一次）
  python YOYOClawCheckin.py --uninstall-task   # 一键解除配置（注销计划任务）

v1.3 变更：
  * 触发方式从"启动文件夹"改为"计划任务"：登录时 + 每天两次。
    启动文件夹只在登录时执行一次，机器长期不注销/只用睡眠时会整天漏跑。
  * 后台运行时不再闪出控制台黑框（tasklist / schtasks / powershell 全部隐藏窗口）。
"""
import base64
import ctypes
import ctypes.wintypes as wt
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import winreg

def _dir(env, tail):
    base = os.environ.get(env) or os.path.join(os.environ["USERPROFILE"], tail)
    return base


DEFAULT_APP_ROOT = r"C:\Program Files\HONOR\MagicClaw"
USER_DATA = os.path.join(_dir("APPDATA", r"AppData\Roaming"), "hclaw")
LOCAL_STATE = os.path.join(USER_DATA, "Local State")
SESSION_BIN = os.path.join(USER_DATA, "billing", "session.bin")

STATE_DIR = os.path.join(_dir("LOCALAPPDATA", r"AppData\Local"), "YOYOClawCheckin")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
LOG_FILE = os.path.join(STATE_DIR, "checkin.log")

BASE_URL = "https://all-scenario-device.rnd.honor.com"
PROCESS_NAME = "HnMagicClawUI.exe"
FP_RE = re.compile(r"^[a-f0-9]{64}$")

VERSION = "1.3"
CREATE_NO_WINDOW = 0x08000000   # 后台运行时不给子进程分配控制台（否则会闪黑框）

WAIT_SESSION_TIMEOUT = 60    # 秒，等客户端刷新登录态（实测约 10 秒）
WAIT_SESSION_POLL = 3

RETRY_TIMES = 3              # 最多尝试 3 次（首次 + 2 次重试）
RETRY_GAP = 15               # 秒，重试间隔
RETRYABLE = {2, 3, 4, 5}     # 6=没装客户端、7=没登录，重试都没意义

FAIL_HINT = {
    2: "等不到 YOYO Claw 刷新登录态。\n\n"
       "请打开 YOYO Claw 确认已登录荣耀账号，然后双击「立即签到.bat」补签。",
    3: "查询签到状态失败（多半是网络不通）。\n\n"
       "联网后双击「立即签到.bat」可以补签。",
    4: "领取积分失败。\n\n可以稍后双击「立即签到.bat」重试。",
    5: "领取已提交，但复核时服务端还没显示已签到。\n\n"
       "建议打开 YOYO Claw 看一眼，多半已经到账。",
    6: "本机没找到 YOYO Claw。\n\n"
       "请确认已安装；装在非默认位置的话，设一个环境变量 YOYOCLAW_ROOT "
       "指向安装根目录（包含 current.json 的那一层）。",
    7: "YOYO Claw 当前没有登录荣耀账号，没法签到。\n\n"
       "请打开客户端登录一次，之后每天就全自动了。",
}


def log(msg):
    line = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def run_hidden(cmd, timeout=60):
    """跑一个命令行程序，不弹黑框。返回 (返回码, stdout, stderr)。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return r.returncode, r.stdout or "", r.stderr or ""
    except Exception as e:
        return -1, "", "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------- 本机解密
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


class AUTH_CIPHER_INFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.ULONG), ("dwInfoVersion", wt.ULONG),
        ("pbNonce", ctypes.POINTER(ctypes.c_char)), ("cbNonce", wt.ULONG),
        ("pbAuthData", ctypes.POINTER(ctypes.c_char)), ("cbAuthData", wt.ULONG),
        ("pbTag", ctypes.POINTER(ctypes.c_char)), ("cbTag", wt.ULONG),
        ("pbMacContext", ctypes.POINTER(ctypes.c_char)), ("cbMacContext", wt.ULONG),
        ("cbAAD", wt.ULONG), ("cbData", ctypes.c_ulonglong), ("dwFlags", wt.ULONG),
    ]


def dpapi_decrypt(blob: bytes) -> bytes:
    src = ctypes.create_string_buffer(blob, len(blob))
    ind = DATA_BLOB(len(blob), src)
    out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(ind), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData 失败: %d" % ctypes.GetLastError())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def aes_gcm_decrypt(key: bytes, nonce: bytes, ct: bytes, tag: bytes) -> bytes:
    b = ctypes.windll.bcrypt
    alg = ctypes.c_void_p()
    if b.BCryptOpenAlgorithmProvider(ctypes.byref(alg), ctypes.c_wchar_p("AES"), None, 0):
        raise OSError("BCryptOpenAlgorithmProvider 失败")
    mode = "ChainingModeGCM"
    if b.BCryptSetProperty(alg, ctypes.c_wchar_p("ChainingMode"),
                           ctypes.c_wchar_p(mode), len(mode) * 2, 0):
        raise OSError("BCryptSetProperty 失败")
    hkey = ctypes.c_void_p()
    if b.BCryptGenerateSymmetricKey(alg, ctypes.byref(hkey), None, 0,
                                    ctypes.create_string_buffer(key, len(key)), len(key), 0):
        raise OSError("BCryptGenerateSymmetricKey 失败")
    info = AUTH_CIPHER_INFO()
    info.cbSize = ctypes.sizeof(info)
    info.dwInfoVersion = 1
    nb = ctypes.create_string_buffer(nonce, len(nonce))
    tb = ctypes.create_string_buffer(tag, len(tag))
    info.pbNonce = ctypes.cast(nb, ctypes.POINTER(ctypes.c_char))
    info.cbNonce = len(nonce)
    info.pbTag = ctypes.cast(tb, ctypes.POINTER(ctypes.c_char))
    info.cbTag = len(tag)
    out = ctypes.create_string_buffer(len(ct))
    n = wt.ULONG(0)
    st = b.BCryptDecrypt(hkey, ct, len(ct), ctypes.byref(info), nb, len(nonce),
                         out, len(ct), ctypes.byref(n), 0)
    b.BCryptDestroyKey(hkey)
    b.BCryptCloseAlgorithmProvider(alg, 0)
    if st:
        raise OSError("BCryptDecrypt 失败: 0x%x" % st)
    return out.raw[:n.value]


def load_session():
    """解密并返回 session 字典；文件不存在时返回 None。"""
    if not os.path.exists(SESSION_BIN):
        return None
    with open(LOCAL_STATE, encoding="utf-8") as f:
        ek = base64.b64decode(json.load(f)["os_crypt"]["encrypted_key"])[5:]
    key = dpapi_decrypt(ek)
    raw = open(SESSION_BIN, "rb").read()[3:]          # 去掉 'v10'
    plain = aes_gcm_decrypt(key, raw[:12], raw[12:-16], raw[-16:])
    obj = json.loads(plain.decode("utf-8"))
    return obj.get("session", obj)


def token_expired(sess) -> bool:
    exp = str(sess.get("expires_at") or "")
    if not exp:
        return False
    try:
        t = dt.datetime.fromisoformat(exp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return dt.datetime.now(dt.timezone.utc) >= t


# ---------------------------------------------------------------- HTTP
def api(session, method, path, body=None):
    url = BASE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + session["access_token"],
        "Content-Type": "application/json",
        "X-Request-Id": str(uuid.uuid4()),
        "X-HCLAW-Device-Fingerprint": session["device_fingerprint"],
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"code": str(e.code), "message": raw[:200]}


def signed_today(data) -> bool:
    today = str(data.get("today") or "")
    if not today:
        return False
    if str(data.get("lastSignDate") or "") == today:
        return True
    for r in data.get("signedRanges") or []:
        if isinstance(r, dict) and r.get("startDate", "") <= today <= r.get("endDate", ""):
            return True
    return False


# ---------------------------------------------------------------- 客户端
def resolve_app_root():
    """定位 YOYO Claw 安装根目录：环境变量 > 卸载表 > 默认路径。"""
    override = os.environ.get("YOYOCLAW_ROOT")
    if override and os.path.isdir(override):
        return override
    for hive, flag in ((winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
                       (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
                       (winreg.HKEY_CURRENT_USER, 0)):
        try:
            key = winreg.OpenKey(
                hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\MagicClaw",
                0, winreg.KEY_READ | flag)
            try:
                loc = winreg.QueryValueEx(key, "InstallLocation")[0].strip().strip('"')
                if loc and os.path.isdir(loc):
                    return loc
            finally:
                winreg.CloseKey(key)
        except OSError:
            continue
    return DEFAULT_APP_ROOT


def find_current_json(start):
    """从 start 向上找 current.json（卸载表可能直接指向版本子目录）。"""
    d = os.path.abspath(start)
    for _ in range(3):
        cfg = os.path.join(d, "current.json")
        if os.path.isfile(cfg):
            return d, cfg
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None, None


def app_path():
    root, cfg = find_current_json(resolve_app_root())
    if not cfg:
        return None
    with open(cfg, encoding="utf-8") as f:
        info = json.load(f)
    rel = info.get("path") or ""
    exe = os.path.join(root, rel)
    # 个别版本 path 已含安装根目录，退一步按文件名兜底
    if not os.path.isfile(exe):
        cand = os.path.join(root, os.path.basename(rel or PROCESS_NAME))
        if os.path.isfile(cand):
            return cand
        return None
    return exe


def app_running() -> bool:
    out = run_hidden(["tasklist", "/FI", "IMAGENAME eq " + PROCESS_NAME], timeout=30)[1]
    return PROCESS_NAME.lower() in out.lower()


def ensure_app_running():
    """确保客户端在跑；返回 False 表示本机没装 / 启动不了。"""
    if app_running():
        log("YOYO Claw 已在运行")
        return True
    exe = app_path()
    if not exe or not os.path.isfile(exe):
        log("未找到 YOYO Claw（检查过卸载表与 %s）。"
            "未安装请先装；装了仍找不到可设环境变量 YOYOCLAW_ROOT 指向安装根目录。" % DEFAULT_APP_ROOT)
        return False
    log("启动 YOYO Claw: %s" % exe)
    subprocess.Popen([exe], cwd=os.path.dirname(exe),
                     creationflags=subprocess.DETACHED_PROCESS)
    return True


def _usable(s):
    return bool(s) and bool(s.get("access_token")) and \
        bool(FP_RE.match(s.get("device_fingerprint") or ""))


def wait_for_fresh_session():
    """返回 (session, code)：0=就绪，7=未登录（别重试），2=在刷新但没赶上。"""
    if not os.path.isfile(SESSION_BIN):
        log("[未登录] 本机没有 YOYO Claw 的登录态文件：%s" % SESSION_BIN)
        log("         请打开 YOYO Claw 登录荣耀账号，然后重跑本程序。")
        return None, 7
    try:
        first = load_session()
    except Exception as e:
        log("[未登录] 本地登录态读不出来：%r" % e)
        return None, 7
    if not _usable(first):
        log("[未登录] 登录态文件里没有账号凭据，YOYO Claw 当前不是已登录状态。")
        log("         请打开 YOYO Claw 登录荣耀账号，然后重跑本程序。")
        return None, 7
    if not token_expired(first):
        log("会话已就绪，expires_at=%s" % first.get("expires_at"))
        return first, 0

    log("登录态已过期，等 YOYO Claw 自动刷新（最多 %d 秒）..." % WAIT_SESSION_TIMEOUT)
    mtime0 = os.path.getmtime(SESSION_BIN)
    deadline = time.time() + WAIT_SESSION_TIMEOUT
    while time.time() < deadline:
        time.sleep(WAIT_SESSION_POLL)
        try:
            s = load_session()
        except Exception:
            continue
        if _usable(s) and not token_expired(s):
            log("会话已刷新，expires_at=%s" % s.get("expires_at"))
            return s, 0

    if os.path.getmtime(SESSION_BIN) == mtime0:
        log("[未登录] %d 秒内 YOYO Claw 一次都没刷新过登录态，"
            "基本可以确定没有登录荣耀账号。" % WAIT_SESSION_TIMEOUT)
        log("         请打开客户端登录，然后重跑本程序。")
        return None, 7
    log("客户端在刷新登录态，但 %d 秒后仍未就绪。" % WAIT_SESSION_TIMEOUT)
    return None, 2


# ---------------------------------------------------------------- 计划任务
TASK_NAME = "YOYOClawCheckin"
TASK_TIMES = ("12:00", "20:00")     # 每天两个固定检查点（配合"错过就补跑"）
LEGACY_VBS = "YOYOClawCheckin.vbs"  # v1.2 及更早的启动文件夹方案，配置时顺手清掉


def startup_dir():
    return os.path.join(_dir("APPDATA", r"AppData\Roaming"),
                        r"Microsoft\Windows\Start Menu\Programs\Startup")


def _pythonw():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("pythonw.exe", "python.exe"):
        cand = os.path.join(here, "python", name)
        if os.path.isfile(cand):
            return cand
    return "pythonw.exe"


def _me():
    """当前 Windows 账号，形如 计算机名\\用户名。"""
    dom = os.environ.get("USERDOMAIN") or os.environ.get("COMPUTERNAME") or ""
    usr = os.environ.get("USERNAME") or ""
    return ("%s\\%s" % (dom, usr)) if (dom and usr) else usr


def _xesc(s):
    """XML 文本转义（路径里可能有 & < > " ）。"""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def task_xml():
    """计划任务定义：登录时触发一次，另外每天 TASK_TIMES 各触发一次。"""
    here = os.path.dirname(os.path.abspath(__file__))
    me = _xesc(_me())
    daily = "".join(
        '    <CalendarTrigger>\n'
        '      <StartBoundary>%sT%s:00</StartBoundary>\n'
        '      <Enabled>true</Enabled>\n'
        '      <ScheduleByDay>\n'
        '        <DaysInterval>1</DaysInterval>\n'
        '      </ScheduleByDay>\n'
        '    </CalendarTrigger>\n' % (dt.date.today().isoformat(), hm)
        for hm in TASK_TIMES)
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.2" '
        'xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        '  <RegistrationInfo>\n'
        '    <Description>YOYO Claw 每日自动签到（登录时 + 每天 %s）</Description>\n'
        '  </RegistrationInfo>\n'
        '  <Triggers>\n'
        '    <LogonTrigger>\n'
        '      <Enabled>true</Enabled>\n'
        '      <UserId>%s</UserId>\n'
        '    </LogonTrigger>\n'
        '%s'
        '  </Triggers>\n'
        '  <Principals>\n'
        '    <Principal id="Author">\n'
        '      <UserId>%s</UserId>\n'
        '      <LogonType>InteractiveToken</LogonType>\n'
        '      <RunLevel>LeastPrivilege</RunLevel>\n'
        '    </Principal>\n'
        '  </Principals>\n'
        '  <Settings>\n'
        '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
        '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n'
        '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n'
        '    <AllowHardTerminate>true</AllowHardTerminate>\n'
        '    <StartWhenAvailable>true</StartWhenAvailable>\n'
        '    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n'
        '    <IdleSettings>\n'
        '      <StopOnIdleEnd>false</StopOnIdleEnd>\n'
        '      <RestartOnIdle>false</RestartOnIdle>\n'
        '    </IdleSettings>\n'
        '    <AllowStartOnDemand>true</AllowStartOnDemand>\n'
        '    <Enabled>true</Enabled>\n'
        '    <Hidden>false</Hidden>\n'
        '    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n'
        '    <WakeToRun>false</WakeToRun>\n'
        '    <ExecutionTimeLimit>PT15M</ExecutionTimeLimit>\n'
        '    <Priority>7</Priority>\n'
        '  </Settings>\n'
        '  <Actions Context="Author">\n'
        '    <Exec>\n'
        '      <Command>%s</Command>\n'
        '      <Arguments>"%s"</Arguments>\n'
        '      <WorkingDirectory>%s</WorkingDirectory>\n'
        '    </Exec>\n'
        '  </Actions>\n'
        '</Task>\n'
        % (" / ".join(TASK_TIMES), me, daily, me, _xesc(_pythonw()),
           _xesc(os.path.join(here, "YOYOClawCheckin.py")), _xesc(here)))


PS_REGISTER = (
    "$ErrorActionPreference = 'Stop'\n"
    "$xml = [System.IO.File]::ReadAllText('%s')\n"
    "Register-ScheduledTask -TaskName '%s' -Xml $xml -Force | Out-Null\n"
    "$t = Get-ScheduledTask -TaskName '%s'\n"
    "$i = Get-ScheduledTaskInfo -TaskName '%s'\n"
    "Write-Output ('STATE=' + $t.State)\n"
    "Write-Output ('TRIGGERS=' + $t.Triggers.Count)\n"
    "if ($i.NextRunTime) { Write-Output ('NEXTRUN=' + "
    "$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')) }\n"
)

PS_QUERY = (
    "$ErrorActionPreference = 'Stop'\n"
    "$t = Get-ScheduledTask -TaskName '%s'\n"
    "$i = Get-ScheduledTaskInfo -TaskName '%s'\n"
    "Write-Output ('STATE=' + $t.State)\n"
    "Write-Output ('TRIGGERS=' + $t.Triggers.Count)\n"
    "if ($i.NextRunTime) { Write-Output ('NEXTRUN=' + "
    "$i.NextRunTime.ToString('yyyy-MM-dd HH:mm:ss')) }\n"
)

PS_UNREGISTER = (
    "$ErrorActionPreference = 'Stop'\n"
    "$t = Get-ScheduledTask -TaskName '%s' -ErrorAction SilentlyContinue\n"
    "if ($null -eq $t) { Write-Output 'EXISTED=0' }\n"
    "else { Write-Output 'EXISTED=1'\n"
    "  Unregister-ScheduledTask -TaskName '%s' -Confirm:$false\n"
    "  Write-Output 'REMOVED=1' }\n"
)


def _run_ps(tag, body):
    """把一段 PowerShell 写成临时脚本再跑（避开命令行转义），返回 (返回码, stdout, stderr)。"""
    path = os.path.join(STATE_DIR, tag + ".ps1")
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(body)
        return run_hidden(["powershell", "-NoProfile", "-NonInteractive",
                           "-ExecutionPolicy", "Bypass", "-File", path], timeout=120)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _cleanup_legacy():
    """删掉 v1.2 的启动文件夹项，避免和计划任务重复运行。"""
    vbs = os.path.join(startup_dir(), LEGACY_VBS)
    if os.path.exists(vbs):
        try:
            os.remove(vbs)
            log("已清理旧版启动项: %s" % vbs)
        except OSError as e:
            log("旧版启动项删不掉（不影响使用，但会重复跑一次）: %r" % e)


def install_task():
    """注册计划任务。返回 (是否成功, 说明字符串)。"""
    xmlpath = os.path.join(STATE_DIR, "task.xml")
    with open(xmlpath, "w", encoding="utf-16") as f:
        f.write(task_xml())

    how, info, err = None, {}, ""
    try:
        rc, out, serr = _run_ps("install", PS_REGISTER % (xmlpath, TASK_NAME, TASK_NAME, TASK_NAME))
        state = re.search(r"^STATE=(.+)$", out, re.M)
        if rc == 0 and state:
            how = "PowerShell Register-ScheduledTask"
            info["state"] = state.group(1).strip()
            m = re.search(r"^TRIGGERS=(\d+)$", out, re.M)
            if m:
                info["triggers"] = m.group(1)
            m = re.search(r"^NEXTRUN=(.+)$", out, re.M)
            if m:
                info["next"] = m.group(1).strip()
        else:
            err = (serr or out).strip()[:300]
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)

    if not how:
        # 退路：PowerShell 用不了时改用系统自带的 schtasks
        rc, out, serr = run_hidden(["schtasks", "/Create", "/TN", TASK_NAME,
                                    "/XML", xmlpath, "/F"], timeout=120)
        if rc == 0:
            how = "schtasks /Create"
            _, out2, _ = _run_ps("query", PS_QUERY % (TASK_NAME, TASK_NAME))
            m = re.search(r"^NEXTRUN=(.+)$", out2, re.M)
            if m:
                info["next"] = m.group(1).strip()
        else:
            err = (serr or out).strip()[:300]

    try:
        os.remove(xmlpath)
    except OSError:
        pass

    if not how:
        return False, "注册计划任务失败：%s" % err
    _cleanup_legacy()
    msg = "已注册计划任务（%s），触发器 %s 个" % (
        how, info.get("triggers", len(TASK_TIMES) + 1))
    if info.get("next"):
        msg += "，下次运行 %s" % info["next"]
    return True, msg


def uninstall_task():
    """注销计划任务。返回 (是否成功, 说明字符串)。"""
    existed, how, err = False, None, ""
    try:
        rc, out, serr = _run_ps("uninstall",
                                PS_UNREGISTER % (TASK_NAME, TASK_NAME))
        if rc == 0 and "EXISTED=" in out:
            existed = "EXISTED=1" in out
            if not existed or "REMOVED=1" in out:
                how = "PowerShell Unregister-ScheduledTask"
        else:
            err = (serr or out).strip()[:300]
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)

    if not how:
        rc, out, serr = run_hidden(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                                   timeout=120)
        if rc == 0:
            existed, how = True, "schtasks /Delete"
        else:
            err = (serr or out).strip()[:300]

    _cleanup_legacy()
    if how:
        return True, ("已解除配置（%s）" % how) if existed else "本来就没有配置过计划任务"
    return False, "注销计划任务失败：%s" % err


# ---------------------------------------------------------------- 主流程
def run_once(dry):
    """跑完整一次签到流程，返回退出码。"""
    today = dt.date.today().isoformat()
    state = {}
    if os.path.exists(STATE_FILE):
        try:
            state = json.load(open(STATE_FILE, encoding="utf-8"))
        except ValueError:
            state = {}
    if state.get("date") == today and state.get("ok"):
        log("今天 (%s) 已成功签到，跳过。grantedPoints=%s" % (today, state.get("grantedPoints")))
        return 0

    if not ensure_app_running():
        return 6

    session, code = wait_for_fresh_session()
    if not session:
        return code

    month = dt.datetime.now().strftime("%Y%m")
    code, resp = api(session, "GET",
                     "/yoyoclaw/points/sign-in/calendar?month=%s" % month)
    if code == 401 or str(resp.get("code") or "").upper() == "UNAUTHORIZED":
        log("[未登录] 服务端拒绝了这个登录态（HTTP=%s code=%s）。"
            "请打开 YOYO Claw 重新登录荣耀账号。" % (code, resp.get("code")))
        return 7
    if code != 200 or not resp.get("data"):
        log("查询失败 HTTP=%s code=%s msg=%s" % (code, resp.get("code"), resp.get("message")))
        return 3
    data = resp["data"]
    log("服务端 today=%s lastSignDate=%s 连续=%s 天" % (
        data.get("today"), data.get("lastSignDate"), data.get("lastSignContinuousDays")))

    if signed_today(data):
        log("今天已经签到过，无需重复领取。")
        state = {"date": today, "ok": True, "grantedPoints": None,
                 "alreadySigned": True, "at": dt.datetime.now().isoformat(timespec="seconds")}
        json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return 0

    if dry:
        log("--dry-run：今天未签到，按要求不执行领取。")
        return 0

    log("今天尚未签到，执行领取 ...")
    code, resp = api(session, "POST", "/yoyoclaw/points/sign-in", {})
    if code != 200 or not resp.get("data"):
        log("领取失败 HTTP=%s code=%s msg=%s" % (code, resp.get("code"), resp.get("message")))
        return 4
    granted = resp["data"].get("grantedPoints")

    code, resp2 = api(session, "GET",
                      "/yoyoclaw/points/sign-in/calendar?month=%s" % month)
    ok = code == 200 and bool(resp2.get("data")) and signed_today(resp2["data"])
    log("领取完成 grantedPoints=%s 复核已签到=%s 连续=%s 天" % (
        granted, ok, (resp2.get("data") or {}).get("lastSignContinuousDays")))

    state = {"date": today, "ok": bool(ok), "grantedPoints": granted,
             "alreadySigned": False, "at": dt.datetime.now().isoformat(timespec="seconds")}
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0 if ok else 5


def alert(msg):
    """开机启动那次是静默的（pythonw 无控制台），只有彻底失败才弹一次窗。"""
    if sys.stdout is not None:          # 手动双击 bat 时已有窗口，不重复打扰
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, "YOYO Claw 自动签到没跑成：\n\n" + msg,
                                         "YOYO Claw 自动签到", 0x30 | 0x10000)
    except Exception:
        pass


def run_with_retries(dry):
    """带重试地跑一次签到，返回退出码。"""
    code = 0
    for attempt in range(1, RETRY_TIMES + 1):
        code = run_once(dry)
        if code == 0 or code not in RETRYABLE:
            break
        if attempt == RETRY_TIMES:
            log("已重试 %d 次仍然失败，放弃。退出码 %d" % (attempt - 1, code))
            break
        log("第 %d 次失败（退出码 %d），%d 秒后重试 ..." % (attempt, code, RETRY_GAP))
        time.sleep(RETRY_GAP)
    return code


def main():
    args = sys.argv[1:]
    os.makedirs(STATE_DIR, exist_ok=True)
    log("YOYO Claw 自动签到 v%s" % VERSION)

    if "--install-task" in args:
        ok, msg = install_task()
        log(msg)
        if not ok:
            return 8
        log("下面立即执行一次签到，验证整条链路是否真的可用 ...")
        code = run_with_retries(False)
        if code == 0:
            log("一键配置成功。以后每天自动运行，不用再管。")
        else:
            log("计划任务已经配好了，但这次立即验证没有通过（退出码 %d），"
                "请看上面的提示处理。" % code)
            alert(FAIL_HINT.get(code, "配置后立即验证失败，退出码 %d。" % code))
        return code

    if "--uninstall-task" in args:
        ok, msg = uninstall_task()
        log(msg)
        if not ok:
            return 8
        log("已停止每日自动签到。日志和记录保留在 %s，不想留就整个删掉。" % STATE_DIR)
        return 0

    code = run_with_retries("--dry-run" in args)
    if code:
        alert(FAIL_HINT.get(code, "自动签到失败，退出码 %d。详情看运行日志。" % code))
    return code


if __name__ == "__main__":
    sys.exit(main())
