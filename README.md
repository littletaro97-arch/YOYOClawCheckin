# YOYOClawCheckin

YOYO Claw（荣耀 MagicClaw）每日积分签到 Windows 小工具。

当前版本：**v1.3**

---

## 使用前请先读

程序会在本机读取 YOYO Claw 保存的登录态，使用 Windows DPAPI 和 BCrypt 解密，再用该登录态向荣耀服务发起签到查询和领取请求。登录态、签到记录和日志保存在本机；源码没有实现向 GitHub 或第三方上传这些数据的功能。签到请求会通过 HTTPS 发往荣耀服务。

本仓库是公开的，因此私有接口地址和本机登录态读取方式也会公开。请先阅读源码，并只在自己电脑上使用自己的荣耀账号。不要代签、批量请求或绕过服务限制。项目为非官方个人项目，与荣耀终端有限公司无关；使用前请确认符合相关服务条款。

---

## v1.3 更新

- 自动运行改为 Windows 计划任务：登录时、每天 12:00 和 20:00 检查。
- 错过触发时间后尽快运行；防止并行实例；使用电池时允许启动。
- 自动运行的控制台子进程不再闪出黑框。
- 「一键配置」注册计划任务后会立即执行一次签到验证。
- 「一键解除配置」注销计划任务。

计划任务使用当前用户的交互式登录会话，因为程序需要 Windows 用户级 DPAPI。用户注销期间不会运行；它不会唤醒电脑。登录触发器会在用户重新登录时执行。

## 工作流程

1. 确保 YOYO Claw 正在运行；若未运行则尝试启动。
2. 等待客户端刷新本机登录态。
3. 查询当天签到状态；当天未签到时才提交一次领取请求。
4. 再查询一次确认结果，并将运行状态与日志写入本机。

程序依赖 YOYO Claw 的本地登录态格式和荣耀的非公开签到接口；任一项改变时都可能失效。

---

## 运行条件

- Windows 10 或 Windows 11，64 位。
- 已安装 YOYO Claw，并已登录自己的荣耀账号。
- 预编译包内置 Python 运行环境，无需另行安装 Python。

## 使用预编译包

在 [Releases](../../releases) 下载 **YOYOClawCheckin-v1.3.zip**，完整解压后使用：

~~~text
YOYOClawCheckin/
  YOYOClawCheckin.py
  python/                   内置 Python 运行环境
  一键配置.bat
  一键解除配置.bat
  立即签到.bat
  查看运行日志.bat
  使用说明.txt
~~~

1. 确认已安装 YOYO Claw 并登录自己的荣耀账号。
2. 双击「一键配置.bat」。它会注册计划任务，并立即执行一次真实签到检查。
3. 查看结果时双击「查看运行日志.bat」。
4. 停止自动运行时双击「一键解除配置.bat」。

请完整解压并保留同目录下的 python 文件夹。运行记录保存在 %LOCALAPPDATA%\YOYOClawCheckin\。

## 计划任务说明

计划任务包含 3 个触发点：用户登录时、每天 12:00、每天 20:00。首次成功后，本机状态会记录当天结果，后续触发只会检查并跳过，不会重复领取。任务需要用户处于登录状态；电脑关机或睡眠时不会运行，也不会主动唤醒电脑。

## 仅运行源码

Windows 上已有 Python 3.9 或更高版本时，可在项目目录运行：

~~~bat
python YOYOClawCheckin.py
python YOYOClawCheckin.py --dry-run
python YOYOClawCheckin.py --install-task
python YOYOClawCheckin.py --uninstall-task
~~~

程序只依赖 Python 标准库。

## 退出码与重试

| 退出码 | 含义 | 自动重试 |
| --- | --- | --- |
| 0 | 成功或今天已经签到 | 否 |
| 2 | 等待登录态刷新超时 | 是 |
| 3 | 查询签到状态失败 | 是 |
| 4 | 领取失败 | 是 |
| 5 | 领取后复核未确认 | 是 |
| 6 | 本机没找到 YOYO Claw | 否 |
| 7 | 没有登录荣耀账号或登录态被拒绝 | 否 |
| 8 | 注册或注销计划任务失败 | 否 |

2、3、4、5 会间隔 15 秒重试，最多重试 2 次（总共最多 3 次）。6、7、8 不重试。

## 常见问题

**报「本机没找到 YOYO Claw」？**

确认客户端已安装。安装在特殊位置时，可设置系统环境变量 YOYOCLAW_ROOT 指向包含 current.json 的安装目录。

**报「没有登录荣耀账号」？**

打开 YOYO Claw 登录自己的账号，然后再运行「立即签到.bat」。

**杀毒软件报警？**

程序需要在本机解密 YOYO Claw 登录态并调用荣耀服务，这类行为可能触发启发式告警。源码是明文，可先审阅 YOYOClawCheckin.py；不要在不信任的机器上运行。

**计划任务注册失败？**

退出码 8 表示计划任务配置失败。确认系统任务计划程序未被组策略禁用，然后查看运行日志。

---

## 自行打包

运行：

~~~bat
python build_package.py
~~~

打包脚本读取仓库中的 package_sources/ 和 YOYOClawCheckin.py。还需准备 Windows 64 位 Python 3.12 embeddable package，并将其完整解压到 dist\YOYOClawCheckin\python\ 后再运行脚本。请保留 Python 发行包中的 LICENSE.txt。脚本会生成 dist\YOYOClawCheckin-v1.3.zip。

## 许可

见 [LICENSE](LICENSE)。
