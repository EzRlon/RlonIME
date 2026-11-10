# RlonIME 全局代码审计报告

| 项 | 内容 |
|---|---|
| 审计日期 | 2026-11-10 |
| 基线 | `54acd41`（docs: README 换 UI 截图），工作树干净；版本 v4.0.3 / dist41 |
| 范围 | `src/**/*.py` 34 文件 13,891 行；`scripts/` 27 脚本 6,530 行；`data/config.json`、`packaging/*`、`requirements.txt`、`README/CHANGELOG/license` |
| 方法 | AST 静态指标（依赖图/函数长度/异常吞噬/危险调用）+ 三路独立只读审查（缺陷 / 安全 / 架构与可维护性）+ 主审计人对高风险项逐条回读源码复核 |
| 性质 | **只读审计，未修改任何代码、未打包、未终止用户进程** |
| 严重度 | 致命（功能不可用或数据损坏）/ 高 / 中 / 低 |
| 优先级 | **立即**（阻断发布）/ **本版**（本迭代必须）/ **下版** / **积压** |

> 口径说明：条目中带 ★ 的为主审计人亲自回读源码/配置确认；其余由独立审查代理给出并附证据行号，已做交叉抽查。第 5 节列出**已核验不成立**的常见怀疑项，避免过度告警。

---

## 零、最需优先关注的高风险问题（Top 10）

| # | 类别 | 问题 | 位置 | 严重度 | 影响范围 | 优先级 |
|---|---|---|---|---|---|---|
| 1 | 缺陷 | ★ 低级键盘钩子回调内，每次按键都执行 `system_ime_composing()`，其兜底判据会做**全局 EnumWindows + 跨进程 ImmGetContext**；代码注释却写「imm32 为微秒级，不违钩子不阻塞红线」——注释与实现矛盾 | `core/ime.py:694-700` ↔ `core/sysime.py:53-65, 68-111` | 致命 | 全部按键路径；窗口多或目标进程无响应时钩子超时被系统静默卸载 → 全局键盘卡顿、输入法整体失效 | 立即 |
| 2 | 缺陷 | ★ 自动重复键造成「放行 keydown 却吞掉唯一 keyup」：组合态吞键并 `reset()` 后 `composing=False`，重复 keydown 走放行分支，但 `vk` 仍在 `_swallowed` 集合，最后那个 keyup 被吞 | `core/ime.py:570-589, 584-589` | 致命 | 长按空格/回车/退格到拼音清空后 → 目标程序收到多个无 keyup 的 keydown，按键粘连、误删文档字符；残留项污染下一次同键 | 立即 |
| 3 | 缺陷 | 兜底热键把 `translate_selection / mark_correction / toggle_panel` 直接注册给 `keyboard` 库，回调在其工作线程**直接调 QWidget 与 Qt 剪贴板**；另两个热键走 Signal 反而是安全的 | `main.py:834-860` | 高 | 钩子未启动时必然走此路 → Qt 跨线程未定义行为、随机崩溃 | 立即 |
| 4 | 缺陷 | 异步翻译写回不校验结果是否仍属于原窗口/选区 | `main.py:398-420, 450-459`；`ui/bar.py:1693-1714` | 高 | 翻译耗时数秒期间用户切窗口/改选区/收起条子 → 译文打进错误窗口、覆盖新选区 | 本版 |
| 5 | 缺陷 | ★ `close_panel()` 不置 `collapsed=True`（只有 ✕/托盘收起会置），托盘单击与右键「隐藏」都走它 | `ui/bar.py:1573-1580` ↔ `main.py` | 高 | 异步翻译结果通过 `show_result()` 把已收起的条子弹回，并可能 `replace_selection()` 覆盖文本——正是 v2.7 声称已修的场景 | 本版 |
| 6 | 安全 | ★ `PrivilegesRequired=lowest` 装到 `%LOCALAPPDATA%\Programs`，`[Files]` 无 ACL，exe/词库/config.json 均可被当前用户改写；叠加 HKCU Run 自启 + 常驻全局钩子 | `packaging/installer.iss:10,46,68`；`core/paths.py:37-48` | 高危 | 任一同用户恶意进程可替换主程序/侧加载 DLL/改 `base_url`，获得开机自启的键盘钩子持久化载体 | 本版 |
| 7 | 安全 | 钩子仅按「前台是否本进程」放行，**无 ES_PASSWORD / 安全桌面 / 远程桌面 / 全屏判定** | `core/ime.py:866-868, 715-738`；`core/capture.py:153` | 高危 | 密码框内字母仍进拼音组合态并由候选窗回显；`Ctrl+Alt+Q` 模拟 Ctrl+C 读剪贴板可把敏感文本发往第三方 | 本版 |
| 8 | 安全/合规 | ★ 随包隐私声明称「**不上传任何用户数据**」「只有主动开启 AI 并填 Key 才发送」「关闭开关（Ctrl+Alt+L）即完全不联网」，实际 `free_translate: true` 默认开启、无同意门、4 家公共接口接收选中文本；声明里的热键也不存在 | `packaging/license.txt:18,21-24` ↔ `data/config.json:590`、`core/free_translate.py:344-349`、`main.py:474-480` | 高危 | 图纸/合同/病历等选区在用户未事先知情下外发乃至出境；声明失实带来合规与法律风险 | 立即 |
| 9 | 安全 | ★ Ⓐ 弹窗保存的 API Key 经 `json.dumps` **明文**写入安装目录 `data/config.json`，升级迁移再 `copy2` 扩散一份 | `main.py:586-590`；`ui/popups.py:427`；`data/config.json:21`；`core/paths.py:111` | 高危 | 任一同用户进程可直接读走 LLM Key（项目自审 `docs/audit_v231_2026-10-16.md:86` 已认账，至今未修） | 本版 |
| 10 | 架构 | ★ OCR 功能**零接线**：`core/ocr.py`(206) + `ui/ocrcap.py`(150) + `ui/ocrpopup.py`(116) 共 472 行，src 内 0 引用；`DEFAULT_HOTKEYS["ocr_capture"]="ctrl+alt+o"` 已注册，但 `on_hotkey()` 只有 5 个分支无 OCR 分支；`ime.set_foreground()` 0 调用；dist41 产物内无 OCR 文件 | `core/config.py:35`；`main.py:462-472`；`core/ime.py:368`；`packaging/zhiyu.spec:48-50` | 高 | 用户按 Ctrl+Alt+O **按键被吞且无任何反应**；配置与注释承诺的功能不存在；472 行死代码进仓库 | 本版 |

> 紧随其后：`seg_codes()` 缓存不随通用词库加载失效导致整句分段永远缺词（`core/lexicon.py:285-302`）、`_split()` 超长无标点段不硬切导致长文本翻译必降级（`core/free_translate.py:352-368`）、`memory.Pair` 共享对象无锁导致学习结果丢失（`core/memory.py:119-207`）——均在正文列出。

---

## 一、低质量 / 难维护代码（"屎山"）

**客观指标**：34 文件 13,891 行；`ui/bar.py` 2,186 行（`CandidateBar` 单类 1,707 行 / 83 方法）、`core/ime.py` 1,037、`core/hierarchy.py` 1,000、`ui/panel.py` 970、`main.py` 866。最长函数 `core/engine.py:107 search()` **244 行**、`ime.py:632 _decide()` 187 行、`panel.py:398 _apply_style()` 163 行。`except Exception` 94 处、其中 **`except …: pass` 33 处**（`main.py` 9、`paths.py` 6）。疑似重复块 25 组（≥8 行同构）；`hierarchy.py` 内 `Position` 模板重复 27 处。版本考古注释 93 行、TODO/FIXME 0。测试 27 脚本 6,530 行：**0 pytest / 0 unittest**，仅 1 个 `def test_`，23 个复制 `sys.path.insert` 样板。

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 处理方向 |
|---|---|---|---|---|---|
| M01 | `core/engine.py:107`、`core/ime.py:632`、`ui/panel.py:398` | 高 | 检索与按键核心路径，难测难改，回归风险集中 | 下版 | 三个 160–244 行函数包揽多阶段流程 → 按阶段拆小函数 + 流水线早返回 |
| M02 | `main.py:740` / `config.py:209` / `bar.py:564` / `hierarchy.py:63-140` | 高 | 改一处漏 N-1 处，配置写盘语义漂移 | 下版 | 同一段「读 config.json→改→写」重复 2 处约 20 行；UI 公共初始化块 4 处约 32 行；`Position` 模板 27 处 → 抽公共函数 / 数据表 / mixin |
| M03 | `main.py:275,323,330,349,603,677,762,766`；`core/paths.py`（6 处） | 高 | 故障无声，线上问题无法定位 | 本版 | 94 个 `except Exception` + 33 个纯 `pass`（`main.py` 14 个 except 中 9 个纯 pass）→ 收窄异常类型 + 结构化日志，禁止裸 pass |
| M04 | `engine.py:495,519,533,538,541`；`config.py:52-61`；`hierarchy.py`；`recommender.py` | 高 | 调权重要翻 4 个文件，改一处排名就漂 | 下版 | 打分权重三套并存且魔法数字硬编码（1.25×42、1.2×29、1.15×24、1.3×21；`_score` 内另有 10/±6/18/100/1000）→ 收进单一 `ScoringPolicy` 常量表 |
| M05 | `config.py:130-131,201,241`；`translate.py:2-17,45,53`；`ime.py:694-696` ★ | 中 | 按注释改代码必错（钩子阻塞、配置只读、热键数量已被注释带偏） | 本版 | 注释与实现矛盾：「线程安全只读」却在写盘、「五个热键」实为 6 个、「五级降级」实有第 6 个 `free` 源、**「imm32 微秒级不阻塞」实为全局枚举** → 注释随实现同步，考古描述移入 CHANGELOG |
| M06 | `main.py:752-753`；`hovercard.py:86-89`；`icons.py:82-84`；全项目 93 行 | 中 | 认知负担，误以为功能仍存在 | 积压 | `_on_committed` 空槽仍被信号连接、2 块注释掉的旧实现、93 行 v2–v4 版本考古注释 → 删死代码，历史归 CHANGELOG |
| M07 | `main.py:148,155,367,387,476,594,621,634`；全项目 `getattr` 74 次 | 中 | 静态检查失效，重构无保护 | 下版 | 93% 函数有类型注解却被 `getattr` 兜底架空（`Any` 11 次），dataclass 配置用字符串键取值 → 属性直取 + 接 pyright/mypy 门禁 |
| M08 | 全项目；`main.py:634` | 中 | 新人上手慢，旧常量误导 | 积压 | 中文注释占 87% 却混用 1–2 字母缩写 `p/k/n/c/d/i/t/zh/vk`；chip/候选、bar/panel/条子混称；兜底版本硬写 `"2.2"` 而实际 4.0.3 → 统一术语表 + 常量集中 |
| M09 | `scripts/` 27 个；`core/ocr.py:195-206` | 中 | 同一假设既写实现又写断言，回归覆盖不可信（QA 两轮已实证盲区） | 下版 | 0 pytest、26 个靠手工 print/断言、41 行重复 setup 样板、模块自带 `__main__` 自测 → 上 pytest + conftest，测试与实现分离 |
| M10 | `main.py:79-122,68`；`ui/icons.py`；`ui/design.py` | 中 | 改品牌色或进程表要同步多份 | 积压 | 托盘图标另用 QPainter 手画、`#34C759` 散落 3 文件、`DEFAULT_CAD_PROCESSES` 与 `config.softwares` 重复 → 收敛单一来源 |

---

## 二、程序缺陷（BUG）

### 2.1 致命 / 高（9 条）

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 最小修法 |
|---|---|---|---|---|---|
| D01 ★ | `core/ime.py:698` / `core/sysime.py:57-65,108` | 致命 | 全局键盘，钩子可能被系统静默卸载 | 立即 | 钩子内做重 API → 检测移出回调，主线程轮询写原子布尔供 `on_key` 读（照 `cad_mode` 模式）；并去掉 `main.py:251-256` 每 500ms 的重复枚举 |
| D02 ★ | `core/ime.py:570-589, 740, 744-818` | 致命 | 长按组合键后按键粘连 / 误删文档字符 | 立即 | 自动重复键「放行 keydown 却吞 keyup」→ `block=False` 的 keydown 分支同步 `_swallowed.discard(vk)`，并加陈旧项清理 |
| D03 | `main.py:846-860` | 高 | 钩子未启动时热键路径，随机崩溃 | 立即 | `keyboard` 工作线程直调 Qt → 三个回调统一改 `Signal.emit`，经队列回主线程 |
| D04 | `core/lexicon.py:285-302` / `main.py:142` | 高 | 通用词库 82k 条不参与整句分段，长拼音不成词 | 本版 | `seg_codes()` 缓存不随 `load_common()/add_custom()` 失效 → 三处置 `self._seg_codes=None` |
| D05 ★ | `ui/bar.py:1573-1580,1722,1769` / `main.py:722-727` | 高 | 收起后被异步结果弹回并覆盖文本 | 本版 | `close_panel()` 统一置 `collapsed=True`；结果回收处校验发起时的前台 HWND |
| D06 | `main.py:398-420,450-459` / `ui/bar.py:1693-1714` | 高 | 译文打进错误窗口 / 覆盖新选区 | 本版 | 发起时记录前台 HWND + 选区文本，主线程回调比对不一致即丢弃（`_busy` 只防并发不防过期） |
| D07 | `core/memory.py:119-141,148-207` | 高 | 学习结果丢失、缓存分数被串改 | 本版 | 查询无锁且返回共享 `Pair` 对象（探针：`a is b` 且 score 被无关调用改写）、`_dirty` 置位/清零在锁外 → 返回副本，`_dirty` 统一进锁 |
| D08 | `core/free_translate.py:352-368,405` | 高 | 长文本翻译必降级（4 家 provider 全失败） | 本版 | `_split()` 对无标点超长段不硬切（1000 字符仍返回 1 段，突破 MyMemory 500B 上限）→ 单 piece 超限按 limit 硬切 |
| D09 | `core/ime.py:815-818` | 高 | 表外符号与拼音错序（出现 `~ch`） | 本版 | 组合态按 `@#$%^&*~` 等仍 `return False` 放行物理键，拼音走 25ms 后异步上屏；v2.7 只修了 8 个表内标点 → 表外符号一并吞键并入 `commit_raw.suffix` |

### 2.2 中（11 条）

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 最小修法 |
|---|---|---|---|---|---|
| D10 | `ui/bar.py:1582-1598` | 中 | 候选条停在 opacity≈0「消失」或位置跳动 | 下版 | 每次新建两个 `QPropertyAnimation` 且不停旧动画、`close_panel` 也不停 → 复用单实例 / start 前 stop / close 时停 `_anim` |
| D11 | `ui/bar.py:1098-1127,1147-1151,2006` | 中 | 拖窄候选条后胶囊被压裁、翻页条宽跳动 | 下版 | 分页三处宽度口径不一致（`_avail_w` vs `CHIPS_AVAIL_W`、`measure()` vs `c.width()`；首枚跳过超宽检查）→ 统一唯一口径 |
| D12 | `main.py:600` / `free_translate.py:233-236,252` | 中 | 改代理后不生效，必须重启 | 下版 | 只更新 `free_translator.proxy` 未同步 `_Fetcher.prefer` 及其 lambda 捕获 → 重建 `_Fetcher` |
| D13 | `core/capture.py:131-181` | 中 | 剪贴板被 Ctrl+C 内容污染、热键重入 | 下版 | 主线程 `sleep+processEvents` 轮询 0.45s 且无 `try/finally` 还原 → 补 finally + 重入锁 |
| D14 | `core/engine.py:32-86` / `core/lexicon.py:322-332` | 中 | 并发写 SQLite 抛 `ProgrammingError`；自定义词反查不到 | 下版 | `UsageStore` 无锁（`check_same_thread=False`）；`add_custom` 不更新 `zh_index/zh_set` → 加 `RLock` + 补索引 |
| D15 | `core/lexicon.py:122-141` | 中 | 配置热重载时候选重复、词频翻倍 | 下版 | `load_all()` 非幂等（探针 2231→4462）→ 开头清空或加 `_loaded` 守卫 |
| D16 | `core/ime.py:240-261` | 中 | 部分成功后整句重试 → 重复上屏 | 下版 | `send_unicode_text` 只按 `sent==len(items)` 判成败 → 返回实际成功字符数，禁止整句重发 |
| D17 ★ | `core/ime.py:848-854` | 中 | `start()` 后再挂一个全局钩子 → 重复吞键/双上屏 | 下版 | `stop()` 在 `join(1.5s)` 超时后强置 `running=False`，旧钩子仍挂 → 显式 `UnhookWindowsHookEx` 并确认线程结束 |
| D18 | `core/ime.py:157,609-620,707-712` | 中 | Shift+数字快捷键在浏览器/Excel/IDE 失效 | 本版 | 中文标点把 `Shift+1/9/0` 吞成 `！（）`，且 `_might_swallow` 恒 True 放大 D01 → 数字键 Shift 组合移出标点表 |
| D19 | `main.py:251-256` | 中 | 主线程周期性卡顿 | 本版 | 每 500ms 再调一次 `system_ime_composing()` → 与 D01 共用同一检测结果 |
| D20 | `core/engine.py` / `core/lexicon.py` | 中 | 自定义词加分/召回异常 | 下版 | `en_short` 历史分虽已封顶 10，但自定义词与 `focus_tags` 在 weak 分支的边界仍不一致 → 单测覆盖 |

### 2.3 低（4 条）

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 最小修法 |
|---|---|---|---|---|---|
| D21 | `core/free_translate.py:406` | 低 | 英→中长文本段间被插空格并上屏到图纸 | 积压 | `" ".join(parts)` → 按目标语言选连接符（zh-CN 用 `""`） |
| D22 | `ui/panel.py:809-837,920-932` | 低 | 旧面板复用/自测时副屏错位、按数字 5 崩 | 积压 | 旧 `_move_to_cursor` 仍用 `物理/scale + primaryScreen()`；`select_index` 未校验越界 → 改用 `logical_point` + clamp（该文件主流程已不引用） |
| D23 | `core/sysime.py:108` | 低 | 64 位下 ctypes 默认转换跨版本可能失效 | 下版 | `EnumWindows` 未声明 `argtypes/restype`（探针 `argtypes=None`）→ 显式声明 |
| D24 | `ui/floatbtn.py:161-163` | 低 | 浮钮可能抢焦点 → SendInput 打到自己 | 本版 | `make_no_activate()` 失败仍无条件置 `_noact=True` 永不重试 → 仅成功时置位 |

---

## 三、潜在安全漏洞与风险点

### 3.1 高危（3 条）

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 缓解 |
|---|---|---|---|---|---|
| S01 ★ | `packaging/installer.iss:10,46,68-83`；`core/paths.py:37-48` | 高危 | 全部装机用户；本地持久化载体 | 本版 | 用户可写安装目录 + HKCU 自启 + 全局钩子 → 改管理员安装到 Program Files，或对 `{app}` 设只读 ACL + 启动校验文件签名 |
| S02 ★ | `core/ime.py:866-868,715-738`；`ui/panel.py:769`；`core/capture.py:153` | 高危 | 密码/涉密文本输入 | 本版 | 无密码框/安全桌面/远程桌面/全屏避让 → 检测输入上下文并全放行，取词前校验前台控件类型 |
| S03 ★ | `main.py:586-590`；`ui/popups.py:427`；`data/config.json:21`；`core/paths.py:111` | 高危 | 填写过 LLM Key 的用户 | 本版 | Key 明文写盘且迁移复制扩散 → DPAPI / Credential Manager 加密 + 收紧 ACL + 迁移后清理旧文件 |

### 3.2 中危（7 条）

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 缓解 |
|---|---|---|---|---|---|
| S04 | `core/llm.py:133,148-156`；`core/recommender.py:327,349-353`；`ui/popups.py:358,369` | 中 | 使用自定义端点的用户（SSRF + Key 外泄） | 本版 | `base_url` 无 scheme/私网校验即拼 `/chat/completions` 并带 `Authorization` → 强制 https、拒绝 loopback/私网/元数据地址、自定义端点二次确认 |
| S05 | `core/free_translate.py:344-349,392-405,198-221` | 中 | 每一段被译文本（重复外发） | 本版 | 〔设计取舍〕4 家 provider × 多路由重试，同一文本被重复发往多家第三方 → 单 provider 单路由、总字符上限、失败降级不换家重发全文 |
| S06 | `core/free_translate.py:84-112,211-221`；`main.py:155-157,387` | 中 | 企业网络 / 数据出境管控 | 下版 | 〔需告知〕「代理留空=自动」会读注册表/环境变量并探测 7890 等本地端口自动改走代理 → 默认关闭，仅用显式填写的代理并展示当前路由 |
| S07 | `core/llm.py:168`；`core/free_translate.py:163`；`core/recommender.py:359` | 中 | 被劫持端点可致进程 OOM | 本版 | 三处 `resp.read()` 无字节上限、不校验 Content-Length → `resp.read(MAX_BYTES)` |
| S08 | `core/lexicon.py:70,207-220,345`；`core/models.py:39-41`；`core/config.py:143` | 中 | 可写目录下的词库/配置被替换 | 下版 | JSON / `common.tsv.gz` 无大小、类型、深度校验，`Term.from_dict` 不校验字段 → gzip bomb / 巨型数组 DoS；加解压后行数上限 + 逐字段校验 |
| S09 | `core/config.py:275,328,341` | 中 | 配置被改即可读安装目录外任意 JSON | 下版 | `lexicon_dir / prof.lexicon` 直接拼接可写配置中的未校验字符串 → 只接受纯文件名并 resolve 限定在目录内 |
| S10 ★ | `requirements.txt:8-21`；`core/ocr.py:83,116-119`；`packaging/zhiyu.spec:48-50` | 中 | 构建可复现性 / 供应链 | 下版 | 依赖全部 `>=` 无 pin/hash/lock；`winrt.*` OCR 依赖未在 requirements 声明、spec 未收集；spec 强打未声明的 `pyperclip/psutil/keyboard` → pin+hash、补齐声明、移除未用 hiddenimports |

### 3.3 低危（3 条）

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 缓解 |
|---|---|---|---|---|---|
| S11 | `core/single_instance.py:34,59-62,82-89` | 低 | 本地骚扰 / 内存占用（非提权） | 积压 | QLocalServer 命名管道无 ACL、`readAll()` 无上限 → 限制 DACL + 读上限 + 命令白名单 |
| S12 | `core/crashlog.py:81-82`；`.github/ISSUE_TEMPLATE/bug_report.md:31,33` | 低 | 用户手工贴日志到公开仓库 | 积压 | 报告含 `sys.executable` 绝对路径与 `platform.platform()` → 脱敏，模板加打码提示 |
| S13 | `core/capture.py:228`；`core/translate.py:648`；`core/memory.py:341`；`core/ocr.py:206` | 低 | 开发/排障时 stdout 泄露 | 积压 | 多处 `__main__` 自检打印选区/译文前 60 字 → 脱敏或置于 `--debug` 后 |

### 3.4 合规风险（3 条，非利用型漏洞）

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 处理 |
|---|---|---|---|---|---|
| S14 ★ | `data/config.json:590`；`ui/onboarding.py:63-145`；`main.py:197,408-414` | 高 | 所有默认安装用户 | 立即 | 免费在线翻译**默认开**、首启引导无同意门，首次按「译」即把选中文本发往腾讯 transmart / 有道 / MyMemory / Google，事后才弹一次 `_free_told` 气泡 → 默认关闭，首次触发前显式 opt-in 并列出接收方与出境说明 |
| S15 ★ | `packaging/license.txt:18,21-24`；`packaging/installer.iss:53`；`core/free_translate.py:344-349` | 高 | 分发合规 / 用户知情权 | 立即 | 隐私声明与实际行为矛盾（详见 Top10 #8）；`Ctrl+Alt+S` 只关 LLM、不关免费翻译 → 按实际行为重写声明，明确 4 家接收方、默认状态与正确关闭方式 |
| S16 | `core/free_translate.py:44,277-287,348`；`data/config.json:590` | 中 | 工程/医疗文本数据出境 | 本版 | 兜底链路含境外端点且默认可达，免费接口条款亦可能留存内容 → 境内/境外分组开关，境外默认关闭并单独告知 |

---

## 四、架构缺陷

**客观结构**：`core.config` 入度 14、`core.models` 11、`ui.design` 8；`main.py` 出度 19（直连几乎所有模块）、`ui/bar.py` 12、`ui/panel.py` 10。循环依赖 2 对（`core.config ↔ core.ime` 靠延迟导入绕过、`ui.bar ↔ ui.hovercard`）。`get_config()` 33 次/14 文件、`.connect()` 63 次/11 文件。孤儿模块 5 个：`core/ocr.py` 206 + `ui/ocrcap.py` 150 + `ui/ocrpopup.py` 116 + `core/msime_bridge.py` 284 + `ui/panel.py` 970（后两者仅 scripts 引用）。

| 编号 | 位置 | 严重度 | 影响范围 | 优先级 | 问题 → 重构方向 |
|---|---|---|---|---|---|
| A01 ★ | `core/ocr.py` / `ui/ocrcap.py` / `ui/ocrpopup.py` / `config.py:35` / `ime.py:368` / `main.py:462-472` | 高 | 472 行死功能 + 一个吞键无动作的热键 | 本版 | OCR **零接线** → 要么补齐「热键→截图→recognize→结果卡→set_foreground」全链（含 spec 收集 winrt），要么整体删除并移除 `ocr_capture` |
| A02 | `ui/bar.py:468` | 高 | 第二上帝对象，与 main 双向耦合 | 下版 | `CandidateBar` 1,707 行 / 83 方法塞进绘制布局、IME 状态、翻译结果、词库重载、拖拽落盘、多屏 DPI → 拆 `BarView` / `BarController` / `BarState` |
| A03 | `main.py:15-22,132-250` | 高 | 全应用无清晰分层，任何改动都穿过主类 | 下版 | docstring 称「架构分层、互不越界」，`ZhiyuApp` 却直连 19 模块并兼管托盘/弹窗/持久化/4 个 QTimer → 引导装配 + Coordinator + Service |
| A04 | `ui/panel.py:198`；`config.py:66` | 高 | 双套 UI 并存，读代码无法判断哪个生效 | 下版 | 旧版 `InputPanel` 970 行主程序 0 引用，config 仍保留 `layout="panel"` 开关 → 确认废弃即删，或抽为可切换的第二 View |
| A05 | `core/config.py:346-353` | 高 | 隐式共享状态、测试无法隔离、并发写无保护 | 下版 | 全局可变单例 `get_config()` 33 处直接读写，docstring 称「线程安全只读」却有多处写回 → 显式注入不可变快照 + 单一 `ConfigStore` |
| A06 | `main.py:740-750,630-636`；`config.py:209-220`；另 10+ 文件 | 高 | 内存态/磁盘态漂移，升级易丢配置 | 本版 | 「读 json→update→write_text」在 `_persist`、`_migrate_legacy_hotkeys` 各写一份 → 收敛到唯一 Persistence 层，带事务 |
| A07 | `main.py:535-544,574-584,239-249` | 中 | 长跑后窗口与信号连接累积 | 下版 | 3 个弹窗每次 new+connect、只 close 不 disconnect/deleteLater，`open_add_word` 却复用单例 → 统一 `PopupRegistry` |
| A08 | `core/config.py:243 ↔ core/ime.py`；`ui/bar.py ↔ ui/hovercard.py` | 中 | 分层失效，导入期错误 | 下版 | 两对循环依赖靠延迟导入绕过 → 热键解析/校验下沉独立 `core/hotkeys.py` |
| A09 | `core/hierarchy.py:59-1000` | 中 | 加一个职位要手写代码 | 下版 | 1,000 行纯数据字面量且数据/逻辑/权重混写 → 外置 `data/industries.json` + 载入器 + 统一权重表 |
| A10 | `config.py:92`；`main.py:139-162,617-628`；`translate.py:45,60-77`；`popups.py`；`bar.py`；`free_translate.py` | 中 | 扩展极易漏接线（OCR 即前车之鉴） | 下版 | 加一种翻译源至少改 6 处；文档「五级」vs 实现「六源」不一致 → 定义 `Source` 协议 + 注册表，开关/来源/label 由注册表驱动 |
| A11 | `config.py:17,21,23`；`main.py:43`；`capture.py:26`；`translate.py:27` | 中 | 打包态/源码态路径易失联 | 下版 | 根路径、版本号、`sys.path` 引导各自定义，注释自认「改版本要同步 3 处」，`PROJECT_ROOT` 双份 → 统一 `core/paths.py`，版本由打包元数据生成 |
| A12 | `main.py:644`；`ui/bar.py`；`translate.py:1-17`；`main.py:834` | 中 | 同一语义多入口多份状态，行为不一致时无法定位 | 下版 | 托盘菜单 vs 候选条图标列、免费翻译 vs LLM vs 本地词库、主钩子 + `keyboard` 兜底双热键通道、三层词库 + 三级细分 → 收敛单一入口 + 显式状态机 |

**最该拆的 3 个文件（最小方案）**：① `ui/bar.py` 2,186 → `BarView`(绘制/布局) + `BarController`(选词/提交状态机) + 图标槽位；② `src/main.py` 866 → 引导装配(<120 行) + 信号路由 Coordinator + `PopupRegistry`；③ `core/hierarchy.py` 1,000 → 数据外置 `data/industries.json` + 载入器(<80 行) + 统一权重表。

---

## 五、已核验不成立（避免过度告警）

- **证书校验未被关闭**：`urllib` 默认校验，`zhiyu.spec:95-97` 保留 `libssl`/`libcrypto`（CPython `_ssl.pyd` 硬依赖），全项目无 `CERT_NONE` / `_create_unverified_context`。
- **SQL 注入**：`core/memory.py` 各语句均为参数化 `?`。
- **运行时代码执行**：生产 `src/` 内无 `eval/exec/pickle.loads/os.system/shell=True`；`subprocess` 仅出现在构建/测试脚本，列表参数无 shell。
- **onedir 临时目录竞态**：PyInstaller onedir 模式无 `_MEIPASS` 解压竞态。
- **击键持久化**：钩子只维护内存 `composing`，不落盘、不记录击键原文；生产日志亦无用户输入原文 —— "输入法本身监控键盘"不计为漏洞（但 **S02 密码框未避让**是具体缺口）。
- **Ⓐ 弹窗 Key 回显**：`popups.py:363` 为 `Password` 掩码，非明文回显；风险在落盘而非界面。
- **静态可编译**：`python -m compileall -q src` rc=0，34 文件无语法错误。

---

## 六、修复路线图（建议分四批，均需按项目红线验证）

| 批次 | 范围 | 理由 | 验收 |
|---|---|---|---|
| **B0 · 阻断发布** | D01 D02 D03、S14 S15、Top10 #8 | 钩子阻塞/按键粘连/跨线程崩溃属功能不可用；隐私声明失实属合规风险，且修复成本最低（改文案 + 默认关 + opt-in） | 钩子回调耗时基准、`_swallowed` 自动重复键断言、热键线程归属、首启同意门 |
| **B1 · 本版必修** | D04–D09、D18 D19 D24、S01–S04 S07 S16、A01 A06、M03 M05 | 都是用户可感知的错误行为或高危暴露面 | 全套 14 套回归（当前 522 条）+ live；改输入内核须**源码版与安装版双验证** |
| **B2 · 下版结构化** | A02–A05 A07–A12、M01 M02 M04 M07 M09、D10–D17 D20 D23 | 拆分上帝对象、统一持久化与配置注入、测试上 pytest | 拆完跑 `test_leak` + `test_common`（词库红线）与 `test_bar_move`（跨屏/拖动红线） |
| **B3 · 积压清理** | M06 M08 M10、D21 D22、S11–S13 | 认知负担与低概率问题 | 随版本顺手清理 |

**施工红线提醒**（沿用项目既有约定）：每轮构建必须用新 `distN`、不加 `--clean`、带 `--upx-dir`；改输入内核后必须验证源码版 + 安装版；候选窗保持 `WS_EX_NOACTIVATE`；上屏只用 `SendInput+KEYEVENTF_UNICODE`；绝不伪造候选；`SetWindowsHookEx` 的 hMod 传 NULL；改常量后必须 grep 确认真的写进文件（v2.7 拖动失效的根因）。

---

## 附录：审计方法与证据

1. **AST 静态扫描**：解析全部 34 文件，统计 import 依赖图、函数/类长度、`ExceptHandler` 形态、危险调用与硬编码凭据；结果见上文客观指标。
2. **三路独立只读审查**：缺陷 / 安全 / 架构各由独立代理执行，允许写临时探针但不改 src；要求每条附证据行号并区分「真实缺陷」与「设计取舍/需告知」。
3. **主审计人复核**：对 Top10 逐条回读源码与配置确认，已确认项标 ★。关键复核包括 `ime.py:694-700` 注释与 `sysime.py:53-111` 实现的矛盾、`ime.py:570-589` 的 `_swallowed` 时序、`bar.py:1573-1580` 的 `collapsed` 缺失、`installer.iss:46` 权限、`license.txt:18-24` 声明、`config.py:35` 与 `main.py:462-472` 的 OCR 空分发、`dist41` 产物内无 OCR 文件。
4. **局限**：本轮为静态 + 进程内探针审计，**未做真机长时压力/多屏混合 DPI 实测、未做渗透测试、未做网络抓包验证数据外发路径**；涉及「境外接收方」的表述基于代码中的端点常量，实际 DNS/链路未抓包。建议 B0/B1 修复后补一轮真机验收。
