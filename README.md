# YOYOClawCheckin

YOYO Claw（荣耀 MagicClaw）每日积分签到的 Windows 自动签到小工具。

开机登录后，在后台检查今天的积分签到有没有领，没领就领一次，然后安静退出。
不依赖屏幕坐标、不依赖窗口大小、不需要管理员权限、不需要安装 Python。

当前版本：**v1.2**

---

## ⚠️ 使用前请先读完这一段

这个工具做的事是：读取**本机** YOYO Claw 客户端已经保存在磁盘上的登录态
（用 Windows DPAPI 解密，和浏览器读取自己的 Cookie 是同一类操作），
然后调用客户端自己会调的那个签到接口，**一天最多一次**。

把这个仓库设为公开，意味着「荣耀私有接口地址」和「从本机客户端取出 access_token 的方法」
也一并公开了。作者不鼓励、也不对下列行为负责：

- 给别人代签、做批量签到、刷积分
- 任何规避服务端限制的改造
- 任何商业用途
- 在荣耀服务条款不允许的场景下使用

**请在自己电脑上、用自己的账号使用。**

本仓库是非官方的个人项目，与荣耀终端有限公司无关。若权利方提出要求，本仓库会配合处理。

---

## 它是怎么工作的（为什么不用鼠标模拟）

常见的自动化思路是按坐标点按钮，但那条路在这里走不通，实测结论：

| 路子 | 结果 |
| --- | --- |
| UI Automation / pywinauto | ❌ 只能看到 Chromium 窗口外壳，DOM 节点数为 0 |
| CDP 远程调试 | ❌ 程序在 asar 里硬编码了检测，发现 `--remote-debugging-port` 直接退出 |
| URI Scheme / COM / 命令行参数 | ❌ 全都没有 |
| 本地 127.0.0.1 服务 | ⚠️ 有，72 条路由，但**没有签到相关路由** |
| 直连私有 API | ✅ 可行 |

所以最终走的是最后一条：

1. 保证 YOYO Claw 在跑（没跑就启动它）
2. 等它自己把登录态刷新到 `%APPDATA%\hclaw\billing\session.bin`
   （这里不自己去刷新 token——refresh token 是一次性的，抢着刷新会把客户端踢下线）
3. 用 DPAPI 解出 `Local State` 里的 AES 主密钥，再解开 session.bin，拿到 access_token 和 device fingerprint
4. `GET /yoyoclaw/points/sign-in/calendar` 查今天签过没有
5. 没签才 `POST /yoyoclaw/points/sign-in`，只发一次
6. 再 GET 一次复核，成功才记录"今天已签"

好处是不受分辨率、DPI、窗口状态影响，客户端小版本更新一般不至于失效。
代价是它依赖本地会话文件的加密格式和那个私有接口——**厂商改了就会失效**，这点要有心理准备。

---

## 前提

1. Windows 10 / 11，64 位
2. 装了荣耀 YOYO Claw（MagicClaw），并且**已经登录荣耀账号**
3. 不需要管理员权限，不需要装 Python

## 用法

### 直接用现成的包（推荐）

到 [Releases](../../releases) 下载 `YOYOClawCheckin-v1.2.zip`，解压到任意位置：

```
YOYOClawCheckin/
  YOYOClawCheckin.py        主程序（明文源码，可审计）
  python/                   精简版 Python 3.12，免安装
  立即签到.bat              手动跑一次
  安装到开机启动.bat         注册静默启动
  取消开机启动.bat
  查看运行日志.bat
  使用说明.txt
```

1. 双击 **立即签到.bat** 先手动跑一次，看到"成功"或"今天已经签到过"
2. 双击 **安装到开机启动.bat**，之后就不用管了
3. 想看每天跑成什么样，双击 **查看运行日志.bat**

> 别只把 bat 拷出来，程序依赖 `python/` 子目录。

### 只用源码

```bat
python YOYOClawCheckin.py                :: 正常执行
python YOYOClawCheckin.py --dry-run      :: 只查询，不领取
python YOYOClawCheckin.py --install-startup
python YOYOClawCheckin.py --remove-startup
```

纯标准库，无第三方依赖，Python 3.9+ 即可。

## 有没有界面

开机自动那次**完全静默**：没有窗口、没有托盘图标。

只有彻底失败（重试完仍失败）才会弹一次窗说明原因。
成功、或者"今天已经签到过"，都不会有任何提示——不打扰是默认行为。

## 退出码

| 码 | 含义 | 是否重试 |
| --- | --- | --- |
| 0 | 成功 / 今天已签到 | — |
| 2 | 等不到登录态刷新 | 是 |
| 3 | 查询签到状态失败 | 是 |
| 4 | 领取失败 | 是 |
| 5 | 领取已提交，但复核时还没显示已签到 | 是 |
| 6 | 本机没找到 YOYO Claw | 否 |
| 7 | 没有登录荣耀账号（或登录态被服务端拒绝） | 否 |

重试策略：间隔 15 秒，最多再试 2 次（首次 + 2 次重试共 3 次）。
6 和 7 不重试，因为再试也没用，需要人去装客户端 / 登录一下。

**只有真正成功才会记录"今天已签"**，所以三次都失败后，当天任何时候手动重跑都能补签。

## 常见问题

**Q：报"没找到 YOYO Claw"？**
确认装了客户端。装在非默认位置的话，设环境变量 `YOYOCLAW_ROOT`
指向安装根目录（包含 `current.json` 的那一层）。

**Q：报"没有登录荣耀账号"？**
打开 YOYO Claw 登录一次，之后就全自动了。

**Q：杀毒软件报警？**
程序要解密本机登录态（DPAPI），这个行为特征容易被启发式扫描误判。
源码是明文的，`YOYOClawCheckin.py` 可以直接看——它不联网上传任何东西，不碰密码。

**Q：客户端升级后会不会失效？**
可能。已知在 `20.0.0.8(SP3)` → `20.0.0.9(SP6C233)` 升级后仍正常工作。

## 自己打包

```bat
python build_package.py
```

会生成 `dist\YOYOClawCheckin\` 和 `dist\YOYOClawCheckin-v1.2.zip`。

打包需要 Windows 版嵌入式 Python 3.12（[python.org 下载](https://www.python.org/downloads/windows/)），
解压后把内容放到 `dist\YOYOClawCheckin\python\` 即可，脚本会自己检查。

## 许可

见 [LICENSE](LICENSE)。简单说：个人自用可以，代签 / 批量 / 商用不行。
