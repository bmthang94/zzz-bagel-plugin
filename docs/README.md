# 贝果计划开发文档

本目录维护贝果插件的自动化实现：流程、识别判据、参数和已知限制。安装与使用说明在仓库根的 [README](../README.md)。改动某一阶段的代码时，同步改对应文档。

## 按任务查找

| 要改或要查的内容 | 文档 |
| --- | --- |
| 一局的完整流程、成功与失败计数、配置字段、暂停与停止时的输入清理 | [流程总览](overview.md) |
| 首次启动恢复、启动清空、快捷手册入口、选图、入场提示、携带与投资核验 | [入场](entry.md) |
| 出生点识别、路线导航、到点与卡位、输入线程、电梯进出 | [出生识别、导航与输入](navigation.md) |
| 小地图镜头朝向识别、转向校准与判稳 | [朝向识别](heading.md) |
| 容器交互、电子保险箱解锁、搜查结果登记、物品识别与优先级、入箱与对换、拖拽 | [开箱与入箱](container.md) |
| 退出与结算提示、入仓核验、出售方案与间隔、返回入口、失败重开 | [退出、入仓、出售与重试](settle.md) |
| 运行日志、收益统计、推送摘要、路线数据记录 | [日志与统计](stats.md) |
| 路线与出生点文件、开启选择、写入规则、分层底图、六昏街南站默认路线 | [路线、出生点与地图](routes.md) |
| 网页路线编辑器的界面、录制、试跑和悬浮窗 | [网页路线编辑器](editor.md) |
| 开房卡模式：房间清单、入口段与房间段、拼接、开启选择和试跑 | [开房卡](keycard.md) |
| 各画面整屏参考帧，角标、朝向、安全箱、出生点等专题素材，以及新增素材的格式规范 | [画面素材](materials/README.md) |
| 界面与日志的英文翻译、词典结构与新增界面文字的步骤 | 本文「界面语言 / i18n」 |

## 验证现状

- 插件仓的 `tests/` 检查插件结构、模块导入、资源路径和扫描接入，也覆盖提示列表和路线交互。识别测试直接复用 [keycard 目录的 WebP 裁剪图](materials/keycard/README.md)，固定 OCR 返回的文字，检查外框和图标。路线测试用模拟帧与时钟检查滚轮及 F 的顺序、次数和停止条件。
- 贝果改为插件前，游戏流程的离线回归位于测试仓 `zzz-od-test` 的 `test/zzz_od/application/bagel/`，测的是主仓内置版本的代码。插件仓没有迁移这批测试。文中「离线测试覆盖……」「离线回归……」的段落描述的是这批测试在内置版本上的结果，不表示插件代码已通过离线回归。
- 各文的「验证范围」「已知限制」列出尚未实机验收的分支。离线回归不能代替实机验收。

## 界面语言 / i18n

插件的界面、日志与路线编辑器跟随一条龙的语言设置（`custom.yml` 的 `ui_language`，
取值 `auto`/`zh`/`en`，在启动时确定，改语言要重启）。中文和没有词典的语言显示原文。

### 机制

- 翻译入口在 `bagel_mac/i18n.py`：
  - `tr('中文原文')`：普通显示文本。
  - `tr_status(text)`：只用于显示状态文本（停止原因、试跑 HUD 状态、悬浮窗节点名、日志里的
    `result.status` 参数）；先精确匹配词典，再按 `{占位符}` 模板匹配（字面文字多的模板优先），
    抓到的值按占位符名处理：`{name}` / `{spawn_name}` / `{room_name}` / `{route_name}` 走 `tr_name`，
    `{names}`（用「、」连起来的名字）逐个 `tr_name`，其余含中文的值递归 `tr_status`
    （所以 `'入箱失败：{v0}'` 里嵌的子状态也会被翻译）。带格式说明的键（`{v0:.0f}`）按已格式化好的文本填入。
  - `tr_n('复数原文', n)`：带数量的文字。英文且 `n == 1` 时先查词典键 `原文|one`，
    没有就回退 `tr()`；中文不变。网页里是 `tn('原文', n)`。只在英文需要单数时才加 `|one` 条目，
    占位符与基础键一致。
  - `tr_name(名字)`：出生点、房间、内置路线名这类**数据名字**的显示翻译。先整串精确匹配，
    否则按 `-`、`_`、` + ` 拆开逐段查词典再按原分隔符拼回，查不到的段原样保留（用户自起的名字不变）。
    网页里是 `tName()`。只用于渲染；比较、查找、查重、保存、新建路线的默认名一律用原名。
  - `web_bootstrap_script()`：把整份词典序列化后注入路线编辑器网页
    （`web/index.html` 的 `<!--BAGEL_I18N-->` 占位符，页面里的 `t()`/`fmt()` 与
    `data-i18n*` 属性消费它）。
- 词典是 `bagel_mac/assets/i18n/<语言>.yml`，扁平映射，键就是代码里的中文原文。
  查找顺序：中文 → 返回原文；其他语言 → 插件词典 → 主程序 `gt` → 原文。

### 规则

- 只在显示时翻译。配置值、路线数据、节点名、`op_name`、`round_*` 返回值、
  `@node_from(status=...)`、`STATUS_*`/`OUTCOME_*` 常量、OCR 目标
  （`gt(..., 'game')`）、用户起的路线名与房间名，一律保持中文原文。
- 状态文本（传给 `round_*` 与各操作里转发状态的小方法，如 `transfer_fail`、`_fail`、`_blocked`）里
  不能写含中文的 f-string：写成 `'模板 {v0}'.format(v0=...)`，并在 `en.yml` 登记同样的模板。
  新增转发状态的小方法时，把它登记到 `tests/test_i18n.py` 的 `STATUS_HELPERS`。
- 点类型显示名用 `route_model.kind_display(kind)`，不要在调用处写 `tr(KIND_LABELS.get(...))`。
- `tr()` 的参数必须是字面量或常量引用：不能用 f-string，也不能传表达式。
  带值的文本写成 `tr('……{name}……').format(name=…)`；日志保留懒加载参数
  `log.info(tr('……%s'), a)`；作为日志参数的状态值用 `tr_status(status)`。
- 键和译文的占位符（`{name}`、`%s` 的数量与名字）必须一致；词典测试会拦。
- 一个字符串既被显示又被比较时，比较的常量保持中文，另写一份 `tr()` 的显示副本。

### 新增一条界面文字

1. 写中文原文，在显示处包 `tr('原文')`（或网页里 `t('原文')` / `data-i18n="原文"`）。
2. 在 `en.yml` 按所在文件的分节加一条 `原文: English text`，占位符对齐。
3. 跑 `pytest tests/`：没有条目、占位符不齐、词典里出现键外的中文都会红。

### 新增一种语言

1. 复制 `en.yml` 为 `assets/i18n/<语言代码>.yml`，逐条替换译文；键保持中文原文不动。
2. `tr()`/`tr_status()`/`web_bootstrap_script()` 会自动按 `current_lang()` 加载，
   代码不用改。
3. 测试现状：`tests/test_i18n.py` 只加载 `en.yml` 做结构与覆盖校验，新语言的词典
   目前不受测试约束；要纳入校验需把 `load_catalog` 相关测试按语言参数化。

### 名字与地名

出生点、房间、内置路线名保持中文（文件、配置、查重都用原名），显示时 `tr_name` 翻译。词典里各部分：

- 官方：录像店 → Random Play（游戏物品文本 “Six Dusk Street - Random Play …”）、风蚀公路 → Windworn Highway、
  巴哈姆特仓库 → Bahamut Warehouse、野人镇 / 无力野人镇 → Bunless Bae Town、风蚀公路汽车旅馆二层 →
  Windworn Highway Motel, 2F（物品名）。
- 取自官方物品描述的片段：仓储中心二层板房 → Storage Center 2F Prefab Building
  （描述 “Bunless Bae Town - Storage Center - Prefab Building 2nd Floor”）。
- 未核实（字面直译或社区叫法）：六昏街南站、白鸽工地站、改建工程区、工地深处、汽车旅馆、芝邦邦餐厅
  （社区叫 Cheezburg Bistro）及各房间入口名。
- 后缀：默认 Default、非高危 Non-High-Risk、入口段 Entry Segment、房间段 Room Segment、低/高 Low/High、
  北/东/西 North/East/West；网页新建路线拼的默认名后缀：自定义 Custom、录制 Recorded、房间段录制
  Room Segment Recording、副本 Copy。

### 术语表

官方英文名（与英文客户端一致）：Operation Bagel、Janus Illusion、Suburbs Illusion、
Standard / Hard / High-Risk、Porcelloy Hub、Papago Hollow、Gear Coins、Briefcase、
Safe、Gear Box、Keycard、Search Supply、Break-Even Beacon、Cacophony。

仍是自拟直译、待校对的词：Loot Run（跑刀）、Keycard Run（开房卡）、
E-Safe / M-Safe / Box（电子箱/机械箱/普通箱）、Polychrome（菲林）、
assist supplies（助战物资）、Zero performance（零号业绩）、
Electronic Safe / Mechanical Safe / Handbag / Cabinet、Tactical Prism、
Tactical Items、waypoint（途径点）、shuffle/short-step（碎步/短步）、
base map（底版）、spawn point（出生点）、`Bagel-<动作>` 式的操作名。

### 测试守卫（tests/test_i18n.py）

- `tr()` 行为：zh 返回原文、en 查词典、未知语言回退 `gt`/原文。
- 扫描约束：`tr()` 首参只能是字面量/常量引用；每个 `tr()`/`t()`/`data-i18n*`
  字面量必须有词典条目；没有未被引用的条目；占位符两侧一致；译文不含键外的
  中文（用户数据透传除外）。
- 日志守卫：`log.*`/`print` 首参出现裸中文即违规（全插件）；`log.x(tr('…%s…'), …)` 的占位符个数要等于参数个数；
  `tr(...)` 的返回值只能接 `.format` / `.join`（挡住 `log.info(tr('…').name)` 这类笔误）。
- 中文字符串守卫（`tests/test_zh_strings.py`）：用 AST 扫描 `bagel_mac/**/*.py` 的每个含中文的字符串常量
  （含 f-string 各段）。文档字符串、`tr/tr_n/tr_name/tr_status/gt` 的首参、装饰器参数、`round_*` 参数、
  `STATUS_*`/`OUTCOME_*` 常量、本身是词典键的字符串不算违规；其余必须翻译，或登记到
  `tests/zh_internal_strings.json`（按 `data_key` / `ocr_target` / `config_value` / `status_match` /
  `game_text_table` / `regex_pattern` 分组，写明每条为什么是内部字符串）。登记项已不存在或已是词典键时测试会红。
- 名字守卫：内置出生点、房间、房间入口与内置路线名按 `tr_name` 拆出的每一段都要有词典条目；
  网页里每个 `<对象>.name` 渲染处都要包 `tName()`，只有比较、查重、新路线默认名、可编辑输入框这几类原名用途
  登记在 `HTML_RAW_NAME_ALLOWED`。
- 复数守卫：`|one` 条目必须有基础键、占位符与基础键一致，且基础键要经 `tr_n` / `tn` 取用。
- 模板守卫：每个带占位符的词典键都能被 `tr_status` 渲染（不抛错、不残留花括号）。
- 状态守卫：`round_*` 参数与 `STATUS_*`/`OUTCOME_*` 常量必须有词典条目
  （`NEVER_DISPLAYED_STATUSES` 可登记永不显示的例外，当前为空）。
- `tr_status`：精确、单/双占位符模板、未收录透传、zh 原样。
- 界面快照：设置页与悬浮窗在 zh 下逐字节等于改版前快照，en 下无中文。
- 网页守卫：`index.html` 恰好一个 `<!--BAGEL_I18N-->`，剥离 `data-i18n*`、
  `t()` 字面量和注释后不得残留中文（白名单只含路线名后缀等用户数据）。

### 已知限制

- 框架自己的日志（节点名、框架状态行）仍是中文；插件 `display_name` 在英文下
  显示为 `Operation [ … ]`。
- 插件商店卡片读 `plugin.json` 的 `name`/`summary`，保持中文，不走词典。
- 合并上游时：上游改了中文原文 → 原文对应的词典键失效，覆盖测试会变红；
  按测试列出的缺失条目补/改 `en.yml` 键即可。
