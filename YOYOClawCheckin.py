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
  python YOYOClawCheckin.py --install-startup   # 注册到"开机启动"
  python YOYOClawCheckin.py --remove-startup    # 取消开机启动
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
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + PROCESS_NAME],
                         capture_output=True, text=True, errors="replace").stdout
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


# ---------------------------------------------------------------- 开机启动
STARTUP_VBS = "YOYOClawCheckin.vbs"


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


def install_startup():
    here = os.path.dirname(os.path.abspath(__file__))
    vbs = os.path.join(startup_dir(), STARTUP_VBS)
    body = ('Set ws = CreateObject("WScript.Shell")\r\n'
            'ws.CurrentDirectory = "%s"\r\n'
            'ws.Run """%s"" ""%s""", 0, False\r\n'
            % (here, _pythonw(), os.path.join(here, "YOYOClawCheckin.py")))
    # VBS 由 WScript 按系统 ANSI 码页读取，中文路径必须写 mbcs
    with open(vbs, "w", encoding="mbcs") as f:
        f.write(body)
    return vbs


def remove_startup():
    vbs = os.path.join(startup_dir(), STARTUP_VBS)
    if os.path.exists(vbs):
        os.remove(vbs)
        return vbs
    return None


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


def main():
    args = sys.argv[1:]
    if "--install-startup" in args:
        log("已注册开机启动: %s" % install_startup())
        return 0
    if "--remove-startup" in args:
        v = remove_startup()
        log("已取消开机启动" if v else "开机启动项本来就不存在")
        return 0

    dry = "--dry-run" in args
    os.makedirs(STATE_DIR, exist_ok=True)

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

    if code:
        alert(FAIL_HINT.get(code, "自动签到失败，退出码 %d。详情看运行日志。" % code))
    return code


if __name__ == "__main__":
    sys.exit(main())
