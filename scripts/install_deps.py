"""依赖安装脚本 — 确保在隔离环境内安装"""
import subprocess
import sys

PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

PKGS = ["pypinyin", "pygetwindow", "psutil", "pyperclip"]

print(f"解释器: {PY}")
r = subprocess.run([PY, "-m", "pip", "install", "--upgrade", "pip"],
                   capture_output=True, text=True)
print("pip 升级 rc =", r.returncode)
if r.returncode != 0:
    print(r.stdout[-3000:]); print(r.stderr[-3000:])

r = subprocess.run([PY, "-m", "pip", "install", *PKGS],
                   capture_output=True, text=True)
print("依赖安装 rc =", r.returncode)
print(r.stdout[-4000:])
if r.returncode != 0:
    print("STDERR:", r.stderr[-4000:])
