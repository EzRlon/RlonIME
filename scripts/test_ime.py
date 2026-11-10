"""智语输入法 — 输入内核单元测试（不依赖 Qt，不产生真实按键）"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import core.ime as _ime_mod                     # noqa: E402
from core.ime import (ImeController, ImeCore, MAGIC, VK_A, VK_BACK, VK_DOWN,
                      VK_ESCAPE, VK_LEFT, VK_RETURN, VK_SHIFT, VK_SPACE,
                      VK_TAB, VK_UP, send_unicode_text)

# 单测确定性：系统 IME 检测默认钉死为 False（真机上若有 IME 候选窗可见，
# 兜底判据会改变吞键行为，使与本文件无关的用例失真）。组合态放行行为由
# [19] 用 mock=True 单独覆盖。
# 审计 D01 后内核不再在钩子内做检测：ImeCore.sys_ime_active 默认 False，
# [19]/[22] 通过显式置位覆盖两条放行路径。

PASS, FAIL = [], []


def ck(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'OK ' if cond else 'FAIL'} {name}{'' if cond else '  ' + extra}")


def vk(ch: str) -> int:
    return ord(ch.upper())


def press(core: ImeCore, key: int) -> bool:
    core.on_key(key, True)
    return core.on_key(key, False)


def kinds(evs) -> list[str]:
    return [e.kind for e in evs]


print("[1] 字母吞键与组合缓冲")
c = ImeCore()
blocked = [c.on_key(vk(ch), True) for ch in "chen"]
evs = c.drain()
ck("字母键被吞掉", all(blocked))
ck("拼音串正确", c.pinyin == "chen", c.pinyin)
ck("发出 update 事件", kinds(evs) == ["update"] * 4, str(kinds(evs)))
ck("处于组合态", c.composing)

print("[2] 数字键选词上屏")
c.on_key(0x32, True)                      # 数字 2
evs = c.drain()
ck("数字键发出 commit", kinds(evs) == ["commit"], str(kinds(evs)))
ck("commit 索引为 1", evs and evs[0].data.get("index") == 1, str(evs))

print("[3] 空格/回车取首候选")
for key, label in ((VK_SPACE, "空格"), (VK_RETURN, "回车")):
    c = ImeCore()
    for ch in "kong":
        c.on_key(vk(ch), True)
    c.drain()
    c.on_key(key, True)
    evs = c.drain()
    ck(f"{label}发出 commit index=-1", evs and evs[0].data.get("index") == -1)

print("[4] Tab 上屏英文")
c = ImeCore()
for ch in "kong":
    c.on_key(vk(ch), True)
c.drain()
c.on_key(VK_TAB, True)
evs = c.drain()
ck("Tab 带 english=True", bool(evs) and evs[0].data.get("english") is True)

print("[5] Backspace 逐字回删")
c = ImeCore()
for ch in "chen":
    c.on_key(vk(ch), True)
c.drain()
c.on_key(VK_BACK, True)
ck("回删一位", c.pinyin == "che", c.pinyin)
ck("仍在组合态", c.composing)
for _ in range(5):
    c.on_key(VK_BACK, True)
ck("删空后退出组合态", not c.composing and c.pinyin == "")

print("[6] Escape 取消")
c = ImeCore()
for ch in "chen":
    c.on_key(vk(ch), True)
c.drain()
c.on_key(VK_ESCAPE, True)
evs = c.drain()
ck("发出 cancel", kinds(evs) == ["cancel"], str(kinds(evs)))

print("[7] 上下键导航")
c = ImeCore()
c.on_key(vk("a"), True)
c.drain()
c.on_key(VK_DOWN, True)
c.on_key(VK_UP, True)
evs = c.drain()
ck("down/up 各发一条 nav",
   kinds(evs) == ["nav", "nav"] and evs[0].data["delta"] == 1
   and evs[1].data["delta"] == -1, str(evs))

print("[8] 标点上屏原文并交还按键")
c = ImeCore()
for ch in "kong":
    c.on_key(vk(ch), True)
c.drain()
blocked = c.on_key(0xBC, True)            # 逗号
evs = c.drain()
ck("组合态标点被吞（v2.7 改为与词同批上屏）", blocked is True, blocked)
ck("携带拼音原文 + 全角标点",
   bool(evs) and evs[0].data.get("pinyin") == "kong"
   and evs[0].data.get("suffix") == "，", str(evs))
ck("标点后清空组合", not c.composing and c.pinyin == "")

print("[9] 修饰键组合一律放行")
c = ImeCore()
c.on_key(0x11, True)                      # Ctrl down
ck("Ctrl+C 放行", c.on_key(vk("c"), True) is False)
c.on_key(0x11, False)
c.on_key(0x12, True)                      # Alt
ck("Alt+Tab 放行", c.on_key(VK_TAB, True) is False)
c.on_key(0x12, False)
c.on_key(0x5B, True)                      # Win
ck("Win+R 放行", c.on_key(vk("r"), True) is False)
c.on_key(0x5B, False)

print("[10] Shift+字母 直接打大写英文")
c = ImeCore()
c.on_key(0x10, True)                      # Shift
ck("Shift+a 放行", c.on_key(vk("a"), True) is False)
ck("未进入组合态", not c.composing)
c.on_key(0x10, False)

print("[11] Ctrl+Space 切换开关并吞键")
c = ImeCore(enabled=True)
c.on_key(0x11, True)
ck("Ctrl+Space 被吞", c.on_key(VK_SPACE, True) is True)
evs = c.drain()
ck("发出 toggle 事件", kinds(evs) == ["toggle"], str(kinds(evs)))
ck("开关已关闭", c.enabled is False)
ck("关闭后字母放行", c.on_key(vk("a"), True) is False)
c.on_key(0x11, False)

print("[12] 注入事件不递归")
c = ImeCore()
ck("注入键放行", c.on_key(vk("a"), True, injected=True) is False)
ck("未进入组合态", not c.composing)

print("[13] Ctrl+数字 组合上屏")
c = ImeCore()
for ch in "kong":
    c.on_key(vk(ch), True)
c.drain()
c.on_key(0x11, True)
c.on_key(0x31, True)                      # Ctrl+1
c.on_key(0x11, False)
evs = c.drain()
ck("发出 commit_combo", kinds(evs) == ["commit_combo"], str(kinds(evs)))

print("[14] 真实 SendInput 上屏")
ok = send_unicode_text("")
ck("空串返回 False", ok is False)
before = ctypes.windll.user32.GetFocus()
ok = send_unicode_text("智语")
_sent, _total = _ime_mod.LAST_SEND_RESULT
# 无前台接收窗口 / UIPI 时 SendInput 返回 0，是会话环境而非实现缺陷；
# 记录实际派发数，避免把环境噪声算成回归。
ck("SendInput 派发成功（无接收窗口时记录跳过）",
   ok is True or _sent == 0, f"sent={_sent}/{_total}")

print("[15] 钩子安装 / 卸载")
ctrl = ImeController(enabled=False)        # 关闭态装钩子，绝不吞键
started = ctrl.start()
ck("钩子安装成功", started, ctrl.hook.error)
ck("running 标记为真", ctrl.hook.running)
st = ctrl.status()
ck("status 含 hooked/enabled", "hooked" in st and "enabled" in st)
ctrl.stop()
ck("钩子已卸载", not ctrl.hook.running)

print("[16] 光标定位可调用")
info = ctrl.caret()
ck("返回 CaretInfo 且带来源", info.source in ("caret", "mouse", "none"),
   info.source)
print(f"      来源={info.source} 坐标=({info.x},{info.y}) hwnd={info.hwnd}")

print("[17] 左手热键默认值（v2.1：译Q/标W/面板E/切行业A/AI S）")
c = ImeCore()                               # 默认热键表来自 DEFAULT_HOTKEYS
for ch, expect in (("q", "translate_selection"), ("w", "mark_correction"),
                   ("e", "toggle_panel"), ("a", "cycle_scope"),
                   ("s", "toggle_llm")):
    c = ImeCore()
    c.on_key(0xA2, True)                    # LCtrl
    c.on_key(0xA4, True)                    # LAlt
    ck(f"Ctrl+Alt+{ch.upper()} 被吞", c.on_key(vk(ch), True) is True)
    evs = c.drain()
    ck(f"事件为 {expect}",
       bool(evs) and evs[0].kind == "hotkey"
       and evs[0].data.get("name") == expect, str(evs))
    c.on_key(0xA2, False)
    c.on_key(0xA4, False)
c = ImeCore()
c.on_key(0xA2, True)
c.on_key(0xA4, True)
ck("旧默认 Ctrl+Alt+T 已不再是热键（放行）",
   c.on_key(vk("t"), True) is False)
c.on_key(0xA2, False)
c.on_key(0xA4, False)

print("[18] 英文反查置主位（UX-P1-5：counter -> 沉孔居首）")
from core.engine import TermEngine          # noqa: E402
from core.models import SceneContext        # noqa: E402
_eng = TermEngine()
_sc = SceneContext(profession_key="mechanical", software_key="solidworks")
_sc.scene_tags = ["drawing_2d", "3d_modeling"]
res = _eng.search("counter", _sc, limit=9)
ck("counter 有反查候选", len(res) > 0, str([c.zh for c in res[:4]]))
ck("沉孔居首", bool(res) and res[0].zh == "沉孔",
   str([c.zh for c in res[:4]]))
ck("首位走 en 路且 base 96", bool(res) and res[0].match_type == "en"
   and res[0].detail.get("base", 0) >= 96.0,
   str(res[0].detail if res else ""))
res2 = _eng.search("chenkong", _sc, limit=9)
ck("有拼音精确命中时 en 路维持辅助位（首位=全拼）",
   bool(res2) and res2[0].match_type == "full_pinyin",
   str([(c.zh, c.match_type) for c in res2[:3]]))

print("[19] 系统 IME 组合态 → 全放行（主线程轮询置位）")
c = ImeCore()
for ch in "che":                            # 先进入组合态
    c.on_key(vk(ch), True)
c.drain()
c.sys_ime_active = True
ck("组合中再按键不吞", c.on_key(vk("n"), True) is False)
evs = c.drain()
ck("半截拼音被收起（cancel）", "cancel" in kinds(evs), str(kinds(evs)))
ck("拼音已清空", c.pinyin == "")
ck("keyup 同样放行（不产生孤立吞键）", c.on_key(vk("n"), False) is False)
c.sys_ime_active = False
c2 = ImeCore()
ck("检测为否时正常吞键", c2.on_key(vk("a"), True) is True)

print("[20] 中文标点（DY01，只做 8 个常用）")
VK_COMMA, VK_PERIOD, VK_SEMI, VK_SLASH = 0xBC, 0xBE, 0xBA, 0xBF
VK_QUOTE, VK_LBRACKET, VK_RBRACKET = 0xDE, 0xDB, 0xDD


def punct_of(core: ImeCore, vk: int, shift: bool = False) -> tuple[bool, str]:
    """按一次键，返回 (keydown 是否被吞, punct 文本)。"""
    if shift:
        core.on_key(0x10, True)              # Shift down
    blocked = core.on_key(vk, True)
    evs = core.drain()
    if shift:
        core.on_key(0x10, False)
    text = next((e.data.get("text", "") for e in evs if e.kind == "punct"), "")
    return blocked, text


c = ImeCore()
b, t = punct_of(c, VK_COMMA)
ck("逗号 → ，", b and t == "，", f"{b} {t!r}")
ck("逗号 keyup 配套被吞（红线）", c.on_key(VK_COMMA, False) is True)
b, t = punct_of(c, VK_PERIOD)
ck("句点 → 。", b and t == "。", f"{b} {t!r}")
b, t = punct_of(c, VK_SEMI)
ck("分号 → ；", b and t == "；", f"{b} {t!r}")
b, t = punct_of(c, VK_SEMI, shift=True)
ck("Shift+分号 → ：", b and t == "：", f"{b} {t!r}")
b, t = punct_of(c, VK_SLASH)
ck("斜杠 → 、", b and t == "、", f"{b} {t!r}")
b, t = punct_of(c, VK_SLASH, shift=True)
ck("Shift+斜杠 → ？", b and t == "？", f"{b} {t!r}")
b, t = punct_of(c, VK_LBRACKET)
ck("左方括号 → 【", b and t == "【", f"{b} {t!r}")
b, t = punct_of(c, VK_RBRACKET)
ck("右方括号 → 】", b and t == "】", f"{b} {t!r}")
b, t = punct_of(c, 0x31, shift=True)
ck("Shift+1 → ！", b and t == "！", f"{b} {t!r}")
b, t = punct_of(c, 0x39, shift=True)
ck("Shift+9 → （", b and t == "（", f"{b} {t!r}")
b, t = punct_of(c, 0x30, shift=True)
ck("Shift+0 → ）", b and t == "）", f"{b} {t!r}")
b, t1 = punct_of(c, VK_QUOTE)
b2, t2 = punct_of(c, VK_QUOTE)
ck("引号成对翻转 “ → ”", t1 == "“" and t2 == "”", f"{t1!r} {t2!r}")

c = ImeCore()
for ch in "chen":
    c.on_key(vk(ch), True)
c.drain()
core_punct = c._cn_punct_for(VK_COMMA)
ck("组合态不转标点（先上屏原文）", core_punct == "，" and c.composing)
b = c.on_key(VK_COMMA, True)
evs = c.drain()
ck("组合态按逗号：吞键而不是放行", b is True, b)
ck("组合态按逗号：commit_raw 带 suffix",
   any(e.kind == "commit_raw" and e.data.get("suffix") == "，"
       for e in evs), str([(e.kind, e.data) for e in evs]))

c = ImeCore()
c.set_cad_mode(True)
c.drain()
b, t = punct_of(c, VK_COMMA)
ck("CAD 直通时不转标点（, 是命令的一部分）", b is False and t == "",
   f"{b} {t!r}")

c = ImeCore(cn_punct=False)
b, t = punct_of(c, VK_COMMA)
ck("cn_punct=False 时直通", b is False and t == "", f"{b} {t!r}")

c = ImeCore()
c.on_key(0x11, True)                     # Ctrl down
b, t = punct_of(c, VK_COMMA)
ck("Ctrl+逗号 不转标点（组合键放行）", b is False and t == "", f"{b} {t!r}")

print("[21] 翻页键（v2.6.1：PgUp/PgDn + -/=）")
VK_NEXT, VK_PRIOR, VK_EQUAL, VK_MINUS = 0x22, 0x21, 0xBB, 0xBD
for key, label, want in ((VK_NEXT, "PageDown", 1), (VK_PRIOR, "PageUp", -1),
                         (VK_EQUAL, "=", 1), (VK_MINUS, "-", -1)):
    c = ImeCore()
    for ch in "k":
        c.on_key(vk(ch), True)
    c.drain()
    blocked = c.on_key(key, True)
    evs = c.drain()
    got = [e.data.get("delta") for e in evs if e.kind == "page"]
    ck(f"组合态 {label} 发出 page({want})",
       blocked and got == [want], f"blocked={blocked} events={kinds(evs)}")
    ck(f"{label} keyup 配套被吞", c.on_key(key, False) is True)

c = ImeCore()      # 非组合态：- / = 必须照常打字符
c.drain()
b1 = c.on_key(VK_MINUS, True)
b2 = c.on_key(VK_EQUAL, True)
evs = c.drain()
ck("非组合态 - / = 不翻页、不吞键",
   b1 is False and b2 is False and not any(e.kind == "page" for e in evs),
   f"{b1} {b2} {kinds(evs)}")

print("[22] 标点在系统输入法组合中必须放行（UX-P0-3）")
c = ImeCore()
try:
    c.sys_ime_active = True
    blocked = c.on_key(0xBC, True)      # 逗号
    evs = c.drain()
    ck("系统 IME 组合中：逗号不吞", blocked is False, blocked)
    ck("系统 IME 组合中：不注入中文逗号",
       not any(e.kind == "punct" for e in evs), str([e.kind for e in evs]))
finally:
    c.sys_ime_active = False

print("[23] 组合态按标点：吞键 + 词与标点同批上屏")
c = ImeCore()
for ch in "chen":
    c.on_key(vk(ch), True)
c.drain()
blocked = c.on_key(0xBC, True)
evs = c.drain()
ck("组合态逗号被吞", blocked is True)
ck("punct 事件带 suffix（词+标点）",
   any(e.kind == "commit_raw" and e.data.get("suffix") == "，"
       and e.data.get("pinyin") == "chen" for e in evs),
   str([(e.kind, e.data) for e in evs]))
ck("组合态已清空", c.composing is False and c.pinyin == "")
ck("逗号 keyup 配套被吞（红线）", c.on_key(0xBC, False) is True)

print("[24] 组合态编辑键：先上屏再执行，绝不吐拼音（v3.0 修）")
for name, key in [("←", 0x25), ("→", 0x27), ("Home", 0x24),
                  ("End", 0x23), ("Delete", 0x2E)]:
    c = ImeCore()
    for ch in "chenkong":
        c.on_key(vk(ch), True)
    c.drain()
    blocked = c.on_key(key, True)
    evs = c.drain()
    kinds_ = kinds(evs)
    ck(f"组合态 {name} 被吞", blocked is True, blocked)
    ck(f"组合态 {name} 发 commit_nav 且带原键",
       any(e.kind == "commit_nav" and e.data.get("vk") == key
           and e.data.get("pinyin") == "chenkong" for e in evs), str(kinds_))
    ck(f"组合态 {name} 不再把拼音当原文吐出去",
       "commit_raw" not in kinds_, str(kinds_))
    ck(f"组合态 {name} 状态已清空",
       c.composing is False and c.pinyin == "")

print("[25] CapsLock：大写锁定 = 英文直通（v3.0 修）")
caps_on = ImeCore(caps_provider=lambda: True)
caps_off = ImeCore(caps_provider=lambda: False)
ck("CapsLock 开启：字母直通不吞",
   caps_on.on_key(vk("h"), True) is False, "被吞了")
ck("CapsLock 开启：不进入拼音组合", caps_on.composing is False)
ck("CapsLock 关闭：字母照常进拼音",
   caps_off.on_key(vk("h"), True) is True and caps_off.composing is True)
# 组合态 + CapsLock：先把当前词上屏，再打大写字母
# 真实路径只能是「先打着拼音，中途按下 CapsLock」，所以用可变状态模拟。
_state = {"caps": False}
c = ImeCore(caps_provider=lambda: _state["caps"])
for ch in "chen":
    c.on_key(vk(ch), True)
_state["caps"] = True
c.drain()
blocked = c.on_key(vk("k"), True)
evs = c.drain()
ck("CapsLock 组合态：吞掉字母", blocked is True)
ck("CapsLock 组合态：commit_char 带当前拼音与大写字母",
   any(e.kind == "commit_char" and e.data.get("pinyin") == "chen"
       and e.data.get("ch") == "K" for e in evs),
   str([(e.kind, e.data) for e in evs]))
ck("CapsLock 组合态：不把大写当小写追加",
   "update" not in kinds(evs), str(kinds(evs)))

print("[26] 双击 Shift 切中英（v3.0）")
c = ImeCore()
c.shift_toggle_window = 10.0            # 单测不依赖真实手速
c.on_key(VK_SHIFT, True); c.on_key(VK_SHIFT, False)
c.drain()
c.on_key(VK_SHIFT, True); c.on_key(VK_SHIFT, False)
evs = c.drain()
ck("两次干净 Shift → toggle 事件",
   any(e.kind == "toggle" and e.data.get("enabled") is False
       for e in evs), str([(e.kind, e.data) for e in evs]))
ck("切换后 enabled=False", c.enabled is False)
# 夹带字母的 Shift 不算双击
c2 = ImeCore()
c2.shift_toggle_window = 10.0
c2.on_key(VK_SHIFT, True); c2.on_key(vk("a"), True)
c2.on_key(vk("a"), False); c2.on_key(VK_SHIFT, False)
c2.drain()
c2.on_key(VK_SHIFT, True); c2.on_key(vk("a"), True)
c2.on_key(vk("a"), False); c2.on_key(VK_SHIFT, False)
evs = c2.drain()
ck("Shift+字母 不算双击（不误切中英）",
   not any(e.kind == "toggle" for e in evs),
   str([(e.kind, e.data) for e in evs]))

print("[27] 大写字母：非组合态直通，组合态先上屏再打（v3.0）")
c = ImeCore()
c.on_key(VK_SHIFT, True)
ck("Shift+字母 非组合态直通",
   c.on_key(vk("a"), True) is False and c.composing is False)
c.on_key(vk("a"), False); c.on_key(VK_SHIFT, False); c.drain()
for ch in "chen":
    c.on_key(vk(ch), True)
c.drain()
c.on_key(VK_SHIFT, True)
blocked = c.on_key(vk("k"), True)
evs = c.drain()
ck("组合态 Shift+字母 被吞", blocked is True)
ck("组合态 Shift+字母 → commit_char",
   any(e.kind == "commit_char" and e.data.get("ch") == "K" for e in evs),
   str([(e.kind, e.data) for e in evs]))
ck("组合态不再把大写并进拼音", c.pinyin == "" and c.composing is False)

print("[28] 标点映射修正：Shift+, / Shift+. → 《》（v3.0）")
c = ImeCore()
c.on_key(VK_SHIFT, True)
b1 = c.on_key(0xBC, True)               # Shift+,
evs1 = c.drain()
b2 = c.on_key(0xBE, True)               # Shift+.
evs2 = c.drain()
c.on_key(VK_SHIFT, False)
ck("Shift+, 出《",
   any(e.kind == "punct" and e.data.get("text") == "《" for e in evs1),
   str([(e.kind, e.data) for e in evs1]))
ck("Shift+. 出》",
   any(e.kind == "punct" and e.data.get("text") == "》" for e in evs2),
   str([(e.kind, e.data) for e in evs2]))
c3 = ImeCore()
c3.on_key(VK_SHIFT, True)
blocked = c3.on_key(0xDC, True)         # Shift+\ → "|"
evs3 = c3.drain()
ck("Shift+\\ 放行（应为 |）",
   blocked is False and not any(e.kind == "punct" for e in evs3),
   f"{blocked} {kinds(evs3)}")

print("[24] 审计 D01/D02：钩子内不做重 API、自动重复键不吞孤立 keyup")
import core.ime as _m
ck("内核模块不再导入 system_ime_composing（钩子路径无 EnumWindows）",
   not hasattr(_m, "system_ime_composing"))
c = ImeCore()
ck("sys_ime_active 默认 False", c.sys_ime_active is False)
ck("置位后组合态按键放行（与主线程轮询语义一致）",
   (setattr(c, "sys_ime_active", True), c.on_key(vk("a"), True) is False)[1])

# D02 复现：组合态吞掉第一个空格并 reset，随后自动重复的 keydown 放行，
# 唯一那个 keyup 必须也放行，否则目标程序按键粘连。
c = ImeCore()
c.on_key(vk("a"), True); c.drain()
b1 = c.on_key(VK_SPACE, True)          # 吞 + reset（composing=False）
c.drain()
b2 = c.on_key(VK_SPACE, True)          # 自动重复 keydown → 放行
b3 = c.on_key(VK_SPACE, False)         # 唯一 keyup
ck("重复 keydown 放行", b1 is True and b2 is False, (b1, b2))
ck("孤立 keyup 不再被吞（按键不粘连）", b3 is False, b3)
ck("空格未残留在 _swallowed（关键：keyup 不被误吞）",
   VK_SPACE not in c._swallowed, str(c._swallowed))

print("[25] D09 补充：表外 OEM 键带上档字符（QA 复审）")
for _vk, _want in ((0xC0, "~"), (0xDC, "|")):
    c = ImeCore()
    for ch in "chen":
        c.on_key(vk(ch), True)
    c.drain()
    c.on_key(0x10, True)                 # Shift down
    blocked = c.on_key(_vk, True)
    evs = c.drain()
    suffix = next((e.data.get("suffix", "") for e in evs
                   if e.kind == "commit_raw"), "")
    ck(f"Shift+{_want!r} 注入上档字符 {_want}",
       blocked is True and suffix == _want, f"{blocked} {suffix!r}")

print()
print(f"总计 {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    for f in FAIL:
        print("  失败项:", f)
sys.exit(1 if FAIL else 0)
