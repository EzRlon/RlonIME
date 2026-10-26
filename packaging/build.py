"""
智语输入法 — 一键构建脚本

流程：
  1. 生成应用图标（纯代码绘制）
  2. PyInstaller 打包 -> dist2/ZhiyuIME/ （若被占用自动换目录）
  3. Inno Setup 编译 -> dist/installer/智语输入法_Setup_vX.exe

用法：
    python packaging/build.py            # 完整构建
    python packaging/build.py --skip-exe # 只重打安装包（复用已有 exe）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packaging"
PY = sys.executable
ISCC_CANDIDATES = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


# ---------------- 1. 图标 ----------------
def make_icon() -> None:
    log("生成应用图标 ...")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = run([PY, str(PKG / "make_icon.py")], env=env)
    if r.returncode != 0:
        log(f"图标生成失败（忽略，使用默认图标）: {r.stderr[-300:]}")
    else:
        log(r.stdout.strip())


# ---------------- 2. exe ----------------
def pick_dist_dir() -> Path:
    """挑一个未占用的产物目录：dist、dist2..distN（沙箱禁删，逐轮用新目录）。

    v3.0 修：旧实现只在 dist..dist20 里找一个"能写"的目录，dist 已存在时
    会直接复用它 —— 复用目录必然触发 COLLECT 删旧目录、被沙箱 safe-delete
    拦截后 exit=1，只在日志尾部露端倪（2026-10-16 两次踩坑）。
    现在改为扫描现有的最大编号 +1，保证每轮都是全新目录。
    """
    base = ROOT / "dist"
    if not base.exists():
        return base
    import re
    nums = [int(m.group(1)) for d in ROOT.glob("dist*") if d.is_dir()
            for m in [re.fullmatch(r"dist(\d+)", d.name)] if m]
    return ROOT / f"dist{max(nums or [1]) + 1}"


def build_exe() -> Path:
    log("PyInstaller 打包 ...")
    dist = pick_dist_dir()
    work = ROOT / ("build" if dist.name == "dist" else f"build_{dist.name}")
    log(f"  产物目录: {dist.name}")
    cmd = [PY, "-m", "PyInstaller", "--noconfirm",
           "--distpath", str(dist), "--workpath", str(work)]
    # D6：UPX 全量压缩（PyInstaller 6 在 spec 执行前计算 upx_available，
    # 只能在命令行显式指定 --upx-dir）
    upx_dir = PKG / "tools" / "upx"
    if (upx_dir / "upx.exe").exists():
        cmd += ["--upx-dir", str(upx_dir)]
        log(f"  UPX: {upx_dir}")
    cmd.append(str(PKG / "zhiyu.spec"))
    r = run(cmd)
    if r.returncode != 0:
        log("打包失败：")
        print((r.stderr or "")[-3000:])
        sys.exit(1)
    out = dist / "ZhiyuIME"
    log(f"  打包完成 -> {out}")
    return out


# ---------------- 3. 安装包 ----------------
def find_iscc() -> str | None:
    for c in ISCC_CANDIDATES:
        if os.path.exists(c):
            return c
    return None


def build_installer(src_dir: Path) -> Path:
    iscc = find_iscc()
    if not iscc:
        log("未找到 Inno Setup (ISCC.exe)，跳过安装包编译。")
        log("安装方式: choco install innosetup -y")
        sys.exit(2)

    # 动态改写 .iss 的 SourceDir / OutputDir 指向本次产物。
    # OutputDir 必须一起改：旧实现只改 SourceDir，安装包永远写到上一个版本的
    # distN/installer 里，版本目录之间会互相污染。
    iss = (PKG / "installer.iss").read_text(encoding="utf-8")
    rel = os.path.relpath(src_dir, PKG)
    out_rel = os.path.relpath(src_dir.parent / "installer", PKG)
    import re
    iss2 = re.sub(r'#define SourceDir ".*?"',
                  lambda m: f'#define SourceDir "{rel}"', iss)
    iss2 = re.sub(r'^OutputDir=.*$',
                  lambda m: f'OutputDir={out_rel}', iss2,
                  flags=re.MULTILINE)
    tmp_iss = PKG / "_installer_build.iss"
    tmp_iss.write_text(iss2, encoding="utf-8")

    log("编译安装向导 ...")
    r = run([iscc, str(tmp_iss)], cwd=str(PKG))
    if r.returncode != 0:
        log("安装包编译失败：")
        print((r.stdout or "")[-3000:])
        sys.exit(1)
    log((r.stdout or "").strip().splitlines()[-1])

    # 安装包输出目录与本次产物 distN 同级（installer.iss OutputDir 指向 distN/installer）
    outdir = src_dir.parent / "installer"
    setups = sorted(outdir.glob("*Setup*.exe"))
    if not setups:
        log("未找到产物 setup.exe")
        sys.exit(1)
    return setups[-1]


def find_latest_build() -> Path | None:
    """在所有 dist* 目录中找最新产出的 ZhiyuIME，按 exe 修改时间排序。"""
    cands = []
    for d in ROOT.glob("dist*"):
        exe = d / "ZhiyuIME" / "ZhiyuIME.exe"
        if exe.exists():
            cands.append((exe.stat().st_mtime, d / "ZhiyuIME"))
    if not cands:
        return None
    cands.sort(reverse=True)
    return cands[0][1]


def main() -> int:
    t0 = time.time()
    skip_exe = "--skip-exe" in sys.argv

    if not skip_exe:
        make_icon()
        src = build_exe()
    else:
        src = find_latest_build()
        if src is None:
            log("未找到已有 exe，请先完整构建")
            return 1
        log(f"复用最新产物: {src.relative_to(ROOT)}")

    setup = build_installer(src)

    total = sum(f.stat().st_size for f in src.rglob("*") if f.is_file())
    log("")
    log("=" * 62)
    log(f"  安装程序: {setup.relative_to(ROOT)}")
    log(f"  安装包大小: {setup.stat().st_size / 1048576:.1f} MB")
    log(f"  解压后占用: {total / 1048576:.1f} MB")
    log(f"  总耗时: {time.time() - t0:.0f}s")
    log("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
