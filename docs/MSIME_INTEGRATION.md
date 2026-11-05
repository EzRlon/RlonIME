# 对接水杉输入法

> 本文说明如何把「RlonIME」的专业术语库接入 **水杉输入法（Metasequoia IME）**，
> 无需编译其 C++ 源码。

---

## 一、先厘清一件事：水杉不是 Rime

很多开源输入法（雾凇拼音、白霜拼音、墨奇等）都基于 Rime 引擎，词库用 `.dict.yaml`。
**水杉输入法不是。**

| | 水杉输入法 |
|---|---|
| 架构 | Windows 原生 **TSF**（Text Services Framework），纯自研 |
| 语言 | C++，依赖 Direct2D/DirectWrite、SQLite、kenlm |
| 与 Rime 关系 | **无依赖**，不能加载 `.dict.yaml` |
| 开源协议 | GPL-3.0 |
| 仓库 | https://github.com/metasequoiaime/MSIME-Windows |
| 数据目录 | `%LOCALAPPDATA%\metasequoiaime` |

所以早期"导出 Rime 词库给水杉用"的思路是错的，该功能已移除。
正确的接入方式是下面的两个官方扩展点。

---

## 二、两个官方扩展点（零编译接入）

### 扩展点 1：词库批量导入

水杉「设置 → 词库」支持查询、新增、编辑、删除和**批量导入**。官方文档原文：

> 拼音和五笔批量文件使用 **Tab 分隔"词语、编码、权重"三列**，因此词语内可保留空格。
> 用户词库可分别导出，便于备份和迁移。

因此 RlonIME 导出 `zhiyu_quanpin_dict.txt`，格式：

```
螺纹孔	luowenkong	950
沉孔	chenkong	900
埋头孔	maitoukong	860
倒角	daojiao	980
```

- **词语**：中文专业术语（内部可含空格，如 `O 型圈`）
- **编码**：全拼，纯小写无空格。含拉丁字母的术语会规范化，如 `BGA 封装 → bgafengzhuang`
- **权重**：0–1000，来自词库的 `freq` 字段，水杉据此排序候选

### 扩展点 2：custom_translations.txt（中译英）

水杉官方支持自定义候选窗翻译。原文规则：

> 在数据目录（默认 `%LOCALAPPDATA%\metasequoiaime\`）下新建 `custom_translations.txt`，
> 每行一条，用 **Tab** 分隔源词和译文。源词含汉字即为中译英，全是英文则为英译中；
> 以 `#` 开头的行是注释；空行忽略；文件用 UTF-8 保存。这份文件优先于内置词库，
> 改完重启输入法生效。

RlonIME 导出的内容形如：

```
# RlonIME · 工程领域专业术语中英对照
螺纹孔	Tapped Hole (GB/T 197) / Threaded Hole
沉孔	Counterbore (GB/T 152.3) / Counter Bore
倒角	Chamfer (GB/T 6403.4) / Bevel
圆角	Fillet / Round
```

这是**工程图英文标注**的核心：在水杉里打出中文，候选窗直接给英文标准表述。

### 扩展点 3（顺带）：AI 联想自定义提示词

水杉 AI 辅助在全拼/双拼输入时异步生成第 3 候选项，支持自定义提示词与
DeepSeek / OpenAI / SiliconFlow / Groq。RlonIME 生成 3 套专业术语导向的提示词，
填入「AI 辅助 → 自定义提示词」即可让联想偏向工程术语而非日常用语。

---

## 三、操作步骤

### 第 1 步：安装水杉输入法

从 Releases 或官网 https://msime.app 下载安装，`Win + 空格` 切换过去。

> 若切换不过去、Server 反复退出，多半是缺少
> **Visual C++ 2015–2022 Redistributable (x64)**。

### 第 2 步：导出对接文件

启动 RlonIME → 候选条顶行点 **Ⓘ（关于）** → 点 **「导出水杉词库」**。

产物位于 `%APPDATA%\RlonIME\msime_export\`：

| 文件 | 用途 |
|---|---|
| `zhiyu_quanpin_dict.txt` | 全部领域合并的全拼词库（618 条），推荐直接导入 |
| `zhiyu_quanpin_mechanical.txt` | 仅机械（210 条），按领域分开管理时用 |
| `zhiyu_quanpin_electrical.txt` | 仅电气（124 条） |
| `zhiyu_quanpin_architecture.txt` | 仅建筑（150 条） |
| `zhiyu_quanpin_electronics.txt` | 仅电子（137 条） |
| `custom_translations.txt` | 中英术语对照（618 条） |
| `msime_ai_prompts.txt` | 3 套 AI 联想提示词 |

### 第 3 步：装中英对照

把 `custom_translations.txt` 复制到 `%LOCALAPPDATA%\metasequoiaime\`，
**重启输入法**生效。

```powershell
Copy-Item "$env:APPDATA\RlonIME\msime_export\custom_translations.txt" `
          "$env:LOCALAPPDATA\metasequoiaime\" -Force
```

### 第 4 步：导入词库

水杉「设置 → 词库 → 批量导入」，选择 `zhiyu_quanpin_dict.txt`。

### 第 5 步：配置 AI 联想（可选）

水杉「实用功能 → AI 辅助」：填入服务商与 API Token，把
`msime_ai_prompts.txt` 里的提示词粘进「自定义提示词」。

---

## 四、两个程序的分工

它们**互不冲突，可以同时安装**：

| | RlonIME | 水杉 |
|---|---|---|
| 角色 | 专业词库的**生成器 + 场景识别工具** | 系统级**输入法本体**（TSF） |
| 场景识别 | 识别 SolidWorks / AutoCAD / Revit 等并切换词库 | 不感知工程软件 |
| 三级身份 | 行业→职业→职位（UI 简化一级，数据三级），影响词库加权 | 不感知职位 |
| 候选窗 | 悬浮独立窗口，SendInput 上屏 | 原生 TSF 候选窗，直接入应用 |
| 词库 | 621 条四领域种子词库 + 自定义 | 导入 RlonIME 导出的词库 |

**推荐用法**：日常打字交给水杉（TSF 原生，任何程序都能用）；
RlonIME 作为词库源与场景工具，按项目需要重新导出、更新水杉的词库。

---

## 五、为什么不做 C++ 融合

fork `MSIME-Windows` 在 `server/` 里加领域词库模块是可行的，但代价：

- 需要 Visual Studio 2026 + C++ 桌面工作负载、CMake 3.25+、vcpkg、Boost、Python 3.10+、Node.js + pnpm 10
- 组件无顶层聚合 CMakeLists，TSF DLL（静态 CRT）与 Server（动态 CRT）需分别构建
- 改 TSF 层后必须跑 `windows/AGENTS.md` 的手工回归清单
- 产物受 **GPL-3.0** 传染
- 词库不在版本库内，需 `python scripts/product_lock.py fetch-dictionaries` 拉取

而两个官方扩展点（词库导入 + custom_translations）已经覆盖了
"专业术语候选" 与 "中文出英文" 这两个核心诉求，**代价接近零**。

若后续确实需要深度耦合（例如让水杉自己感知当前工程软件），再做 fork。

---

## 六、维护与更新

词库内容更新后，重新执行「导出水杉词库」，再在水杉里重新导入即可。
`custom_translations.txt` 覆盖同名文件后重启输入法。

自定义词条（候选条顶行 **＋** 添加）也会一并进入导出结果。
