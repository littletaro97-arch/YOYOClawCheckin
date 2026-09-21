# -*- coding: utf-8 -*-
"""打包脚本：从仓库源码和 package_sources 生成 Windows 分发包。

用法：
    python build_package.py

产物：
    dist\\YOYOClawCheckin\\
    dist\\YOYOClawCheckin-v1.3.zip

打包前，将官方 Windows 64 位 Python embeddable package 解压到：
    dist\\YOYOClawCheckin\\python\\

打包脚本不会下载或改写 Python 运行时文件。
"""
import os
import shutil
import zipfile

VERSION = "1.3"
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "YOYOClawCheckin.py")
TEMPLATE = os.path.join(HERE, "package_sources")
DIST = os.path.join(HERE, "dist")
DST = os.path.join(DIST, "YOYOClawCheckin")
ZIP = os.path.join(DIST, "YOYOClawCheckin-v%s.zip" % VERSION)

PACKAGE_FILES = (
    "一键配置.bat",
    "一键解除配置.bat",
    "立即签到.bat",
    "查看运行日志.bat",
    "使用说明.txt",
)
LEGACY_FILES = (
    "安装到开机启动.bat",
    "取消开机启动.bat",
)


def main():
    if not os.path.isfile(SRC):
        raise SystemExit("找不到主程序: %s" % SRC)
    if not os.path.isdir(TEMPLATE):
        raise SystemExit("找不到分发文件模板目录: %s" % TEMPLATE)

    for name in PACKAGE_FILES:
        path = os.path.join(TEMPLATE, name)
        if not os.path.isfile(path):
            raise SystemExit("缺少分发文件模板: %s" % path)

    os.makedirs(DST, exist_ok=True)
    shutil.copy2(SRC, os.path.join(DST, "YOYOClawCheckin.py"))

    for name in PACKAGE_FILES:
        shutil.copy2(os.path.join(TEMPLATE, name), os.path.join(DST, name))

    # 清除 v1.2 及更早版本遗留的启动文件，避免旧 bat 与计划任务并存。
    for name in LEGACY_FILES:
        path = os.path.join(DST, name)
        if os.path.isfile(path):
            os.remove(path)

    py_dir = os.path.join(DST, "python")
    pyexe = os.path.join(py_dir, "python.exe")
    license_file = os.path.join(py_dir, "LICENSE.txt")
    if not os.path.isfile(pyexe):
        print("[跳过] 没找到 %s，未生成 zip。" % pyexe)
        print("       将 Python 3.12 embeddable package 解压到该目录后重新运行。")
        return
    if not os.path.isfile(license_file):
        raise SystemExit("缺少 python\\LICENSE.txt；请保留 Python 发行包许可证。")

    if os.path.exists(ZIP):
        os.remove(ZIP)
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for root, dirs, files in os.walk(DST):
            dirs[:] = [name for name in dirs if name != "__pycache__"]
            for name in files:
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(root, name)
                relative = os.path.relpath(full, DIST)
                archive.write(full, relative)

    print("[完成] %s (%.1f MB)" % (ZIP, os.path.getsize(ZIP) / 1048576))


if __name__ == "__main__":
    main()
