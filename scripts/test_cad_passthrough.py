"""A06-2 CAD 直通语义回归（M2 / S1-A）

验收口径：
  1. CAD 前台默认**不吞字母**（L/C/M/E 是命令），也不进入组合态
  2. Ctrl+Space 只做「临时激活」，不切全局开关；激活后正常出拼音
  3. 临时激活空闲超时（默认 5s）自动回落，正在拼的字被取消
  4. 离开 CAD（前台切走）立即恢复接管
  5. CAD 直通不影响应用级热键（Ctrl+Alt+Q 等照常触发）

    python scripts/test_cad_passthrough.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import core.ime as _ime_mod                    # noqa: E402
from core.ime import ImeCore, VK_A, VK_SPACE   # noqa: E402

_ime_mod.system_ime_composing = lambda *a, **k: False   # 单测确定性

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK  ' if cond else 'FAIL'} {name}"
          f"{'' if cond else '  ' + str(extra)}")


def vk(ch: str) -> int:
    return ord(ch.upper())


def press(core: ImeCore, key: int) -> bool:
    """按下+抬起，返回 keydown 是否被吞。"""
    blocked = core.on_key(key, True)
    core.on_key(key, False)
    return blocked


def kinds(evs) -> list[str]:
    return [e.kind for e in evs]


print("[1] CAD 前台：字母默认直通")
c = ImeCore()
c.set_cad_mode(True)
c.drain()
blocked = [press(c, vk(ch)) for ch in "lcm"]
ck("字母未被吞（L/C/M 是 CAD 命令）", not any(blocked), blocked)
ck("不进入组合态", c.composing is False and c.pinyin == "")
ck("无 update 事件", kinds(c.drain()) == [])

print("[2] Ctrl+Space：临时激活（不切全局开关）")
c.on_key(0x11, True)                     # Ctrl down
blocked = c.on_key(VK_SPACE, True)
ck("Ctrl+Space 被吞（避免系统输入法同时触发）", blocked)
ck("全局开关保持开启（不是 toggle）", c.enabled is True)
ck("进入临时激活态", c.cad_temp_active())
evs = c.drain()
ck("发出 cad(temp) 事件",
   any(e.kind == "cad" and e.data.get("temp") for e in evs), kinds(evs))
c.on_key(0x11, False)                    # Ctrl up（放行）
ck("Ctrl 抬起不被吞", c.on_key(0x11, False) is False)

print("[3] 临时激活期间正常拼字")
blocked = [press(c, vk(ch)) for ch in "chen"]
ck("字母被吞", all(blocked), blocked)
ck("拼音累积", c.pinyin == "chen", c.pinyin)
ck("处于组合态", c.composing)
c.drain()

print("[4] 空闲超时 → 自动回落，半截拼音被取消")
c._cad_last = time.monotonic() - (c.cad_idle + 0.5)
blocked = press(c, vk("k"))
evs = c.drain()
ck("超时后字母重新直通", blocked is False, blocked)
ck("临时激活已结束", c.cad_temp_active() is False)
ck("半截拼音被取消（cancel）", "cancel" in kinds(evs), kinds(evs))
ck("组合态已结束", c.composing is False and c.pinyin == "")

print("[5] 持续输入续命（不会边打边掉）")
c = ImeCore()
c.set_cad_mode(True)
c.on_key(0x11, True)
c.on_key(VK_SPACE, True)
c.on_key(0x11, False)
c.drain()
for _ in range(3):
    press(c, vk("a"))
    c._cad_last = time.monotonic() - (c.cad_idle - 0.5)   # 逼近超时但未超
    ck("仍在临时激活", c.cad_temp_active())
c.drain()

print("[6] 离开 CAD：立即恢复接管")
c = ImeCore()
c.set_cad_mode(True)
c.on_key(0x11, True)
c.on_key(VK_SPACE, True)
c.on_key(0x11, False)
c.drain()
c.set_cad_mode(False)
evs = c.drain()
ck("退出临时激活", c.cad_temp_active() is False)
ck("发出 cad(off) 事件", any(e.kind == "cad" for e in evs), kinds(evs))
blocked = press(c, vk("c"))
ck("字母重新被吞", blocked is True, blocked)
ck("拼音正常累积", c.pinyin == "c", c.pinyin)

print("[7] CAD 直通不吃应用级热键")
c = ImeCore()
c.set_cad_mode(True)
c.drain()
c.on_key(0x11, True)      # Ctrl
c.on_key(0x12, True)      # Alt
blocked = c.on_key(vk("q"), True)          # Ctrl+Alt+Q = 选中即译
evs = c.drain()
ck("Ctrl+Alt+Q 被吞", blocked is True, blocked)
ck("发出 hotkey 事件",
   any(e.kind == "hotkey" and e.data.get("name") == "translate_selection"
       for e in evs), kinds(evs))
c.on_key(vk("q"), False)
c.on_key(0x12, False)
c.on_key(0x11, False)

print("[8] 非 CAD 前台：Ctrl+Space 仍是全局开关")
c = ImeCore()
c.set_cad_mode(False)
c.drain()
c.on_key(0x11, True)
c.on_key(VK_SPACE, True)
c.on_key(0x11, False)
ck("开关已切换为关闭", c.enabled is False)
blocked = press(c, vk("a"))
ck("关闭后字母直通", blocked is False, blocked)

print("[9] 暂停接管优先于 CAD 直通")
c = ImeCore()
c.set_cad_mode(True)
c.set_enabled(False)
c.drain()
ck("暂停时字母直通", press(c, vk("a")) is False)
c.on_key(0x11, True)
c.on_key(VK_SPACE, True)
c.on_key(0x11, False)
ck("CAD 下 Ctrl+Space 不越过暂停（仍是临时激活）",
   c.cad_temp_active() is True and c.enabled is False)

print()
print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
for f in FAIL:
    print("  失败项:", f)
sys.exit(1 if FAIL else 0)
