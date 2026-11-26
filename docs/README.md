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
  - `tr_status(text)`：只用于显示状态文本（停止原因、试跑 HUD 状态、日志里的
    `result.status` 参数）；先精确匹配词典，再按 `{占位符}` 模板匹配，匹配到的值原样填入。
  - `web_bootstrap_script()`：把整份词典序列化后注入路线编辑器网页
    （`web/index.html` 的 `<!--BAGEL_I18N-->` 占位符，页面里的 `t()`/`fmt()` 与
    `data-i18n*` 属性消费它）。
- 词典是 `bagel_mac/assets/i18n/<语言>.yml`，扁平映射，键就是代码里的中文原文。
  查找顺序：中文 → 返回原文；其他语言 → 插件词典 → 主程序 `gt` → 原文。

### 规则

- 只在显示时翻译。配置值、路线数据、节点名、`op_name`、`round_*` 返回值、
  `@node_from(status=...)`、`STATUS_*`/`OUTCOME_*` 常量、OCR 目标
  （`gt(..., 'game')`）、用户起的路线名与房间名，一律保持中文原文。
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
- 日志守卫：`log.*`/`print` 首参出现裸中文即违规（全插件）。
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
