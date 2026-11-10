# -*- mode: python ; coding: utf-8 -*-
"""
RlonIME — PyInstaller 打包配置（v2.2 D6 体积优化）

产物：
  distN/RlonIME/RlonIME.exe      主程序（onedir，启动快、杀软误报低）
  然后由 Inno Setup 打成 setup.exe 安装向导

D6 口径（用户拍板：全量 UPX）：
  - spec 排除：opengl32sw（20MB 软渲染兜底）、QML 栈、虚拟键盘、Qt6Pdf/Qt6Svg
    （依赖链已核验：保留件均不引用以上任一；裁剪口径见下方注释）
  - .qm 翻译只留 zh_CN（96 → 3 个文件）
  - UPX 全量压缩 dll/pyd；UPX 位于 packaging/tools/upx（缺失则自动不压缩）
"""
import os
from PyInstaller.utils.hooks import collect_submodules

# SPECPATH 指向本 spec 所在目录（packaging/），项目根在上一级
ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# ---- D6：UPX 目录 ----
# 注意：PyInstaller 6.x 在**执行本 spec 之前**就已计算 upx_available，
# spec 内改 CONF 无效。构建命令必须显式带：
#     --upx-dir packaging/tools/upx
# （packaging/build.py 已自动附加；手工构建见 README 打包一节）

# 需要一并打包的只读资源：词库（领域词库 + 工程通用词库）与通用词库数据
# 注：水杉输入法不依赖 Rime，无需打包 .dict.yaml
datas = [
    (os.path.join(ROOT, "data", "lexicons"), "data/lexicons"),
    (os.path.join(ROOT, "data", "guide"), "data/guide"),
]
# v2.4.0 技术要求句库（S3）：短码 yq01-yq50，中英对照
_phrases = os.path.join(ROOT, "data", "phrases")
if os.path.isdir(_phrases):
    datas.append((_phrases, "data/phrases"))
# v2.3 通用词库（常规输入）：约 8 万条 / 1.5MB gz
_common = os.path.join(ROOT, "data", "common")
if os.path.isdir(_common):
    datas.append((_common, "data/common"))
_cfg = os.path.join(ROOT, "data", "config.json")
if os.path.exists(_cfg):
    datas.append((_cfg, "data"))
_assoc = os.path.join(ROOT, "data", "associations.json")
if os.path.exists(_assoc):
    datas.append((_assoc, "data"))

hiddenimports = (
    collect_submodules("pypinyin")
    + ["keyboard", "pyperclip", "psutil", "pygetwindow"]
    # v4.1：Windows 内置 OCR（winrt 命名空间包是动态导入，PyInstaller 扫不到）
    + collect_submodules("winrt")
)

a = Analysis(
    [os.path.join(ROOT, "src", "main.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 界面与计算类重型依赖：本程序一个都不用
        "tkinter", "matplotlib", "numpy", "pandas", "scipy",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia", "PySide6.QtQml", "PySide6.QtQuick",
        "PySide6.Qt3DCore", "PySide6.QtBluetooth", "PySide6.QtDesigner",
        "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtHelp",
        "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
        "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSensors",
        "PySide6.QtSerialPort", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
        "PySide6.QtWebChannel", "PySide6.QtWebSockets",
        # 防环境残留：PyQt 全家与旧版 PySide 一律不进产物
        # （注意：不排除 PySide6.QtNetwork，单实例 QLocalServer 需要它）
        "PyQt6", "PyQt5", "PySide2",
        # 打包工具链自身：被 PyInstaller 顺带收进来的，纯属体积负担
        "setuptools", "pip", "pkg_resources", "wheel",
        "distutils", "lib2to3", "unittest", "pydoc_data", "test",
        # 本项目未使用的标准库重模块
        "asyncio", "concurrent.futures.process", "xmlrpc", "pdb",
        "doctest", "curses", "sqlite3.test",
    ],
    noarchive=False,
)

# ============ D6 体积优化：裁剪未用 Qt 组件（依赖链已逐一核验） ============
#  opengl32sw.dll  20MB 软渲染兜底；纯 QWidget 光栅渲染不创建 OpenGL 上下文
#  Qt6VirtualKeyboard + qtvirtualkeyboardplugin → 拖入整条 QML 栈，故一并裁掉
#  Qt6Quick / Qt6Qml*  QML 运行时（上一条的根因，双保险）
#  Qt6Pdf / Qt6Svg 仅被 qpdf/qsvg 图像插件引用，插件一并裁掉
#  Qt6OpenGL 无控件使用（Qt6Widgets/Gui/Core 均不静态引用）
#
# v2.5 体积审计（SZ01/SZ02，实测 56.0 MB → 目标 ≈47.6 MB）：
#  · TLS 插件 plugins/tls/*（0.64 MB）：QtNetwork 只被单实例 QLocalServer
#    （本地管道）使用，不走 TLS → 可裁。
#    ⚠️ **libssl-3-x64.dll / libcrypto-3-x64.dll 千万不能裁**：它们是 CPython
#    自己的 OpenSSL（`_ssl.pyd` 直接 import 这两个名字），AI 翻译走 urllib
#    https 需要它们。实测已核（_ssl.pyd 的导入表命中）。
#  · 图像格式插件 imageformats/* 1.74 MB：托盘图标是 QPainter 代码绘制，
#    引导页是 PNG（QtGui 内建解码），一个都不需要。
#  · 平台插件只留 qwindows.dll；qdirect2d(1.02MB)/qminimal/qoffscreen 永不使用。
#  · iconengines/qsvgicon、networkinformation、generic/qtuiotouch 也用不到。
_CUT_BIN = ("opengl32sw", "Qt6Quick", "Qt6Qml", "Qt6VirtualKeyboard",
            "Qt6Pdf", "Qt6Svg", "Qt6OpenGL",
            "qtvirtualkeyboardplugin", "qpdf.dll", "qsvg.dll",
            # SZ01：Qt 侧 TLS 后端（注意：不是 libssl/libcrypto 本身）
            "qopensslbackend", "qschannelbackend", "qcertonlybackend",
            # SZ02：无用 Qt 插件
            "qjpeg", "qwebp", "qtiff", "qgif", "qicns", "qtga", "qwbmp",
            "qico", "qdirect2d", "qminimal", "qoffscreen", "qsvgicon",
            "qnetworklistmanager", "qtuiotouchplugin")
a.binaries = [b for b in a.binaries
              if not any(t in str(b[0]) for t in _CUT_BIN)]

# 96 个 Qt 翻译 .qm 只留中文（qt/qtbase/qt_help 的 zh_CN 共 3 个）
a.datas = [d for d in a.datas
           if not str(d[0]).endswith(".qm")
           or "zh_CN" in str(d[0])]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RlonIME",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                # D6：全量 UPX（bootloader 已压，自动跳过）
    upx_exclude=[],
    console=False,           # 无控制台窗口（托盘常驻程序）
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "packaging", "zhiyu.ico")
    if os.path.exists(os.path.join(ROOT, "packaging", "zhiyu.ico")) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,                # D6：全量 UPX（dll/pyd）
    upx_exclude=[],
    name="RlonIME",
)
