# 贝果计划插件 — macOS 工作日志

> 交接用。记录 macOS (Apple Silicon / PlayCover) 适配与 ID 分离。

## 目标

为绝区零一条龙（macOS 专用分支）提供贝果计划自动跑刀插件支持。

## 架构与布局

```
plugins/zzz_bagel_plugin_bmthang94/   仓库根（github.com/bmthang94/zzz-bagel-plugin，branch main）
├── bagel_mac/                        插件包，目录名 = APP_ID
│   ├── const.py                      APP_ID = "bagel_mac", APP_NAME = "贝果计划 (macOS)"
│   ├── plugin.json                   id = "bagel_mac", name = "贝果计划 (macOS)", versionCode = 13
│   ├── factory.py                    应用工厂类
│   ├── app.py                        状态机实现
│   ├── screen_info/                  画面区域定义（app_id: bagel_mac）
│   ├── operation/                    入场、移动、开箱、整理背包、撤离结算等操作
│   └── route/                        寻路与打点录制
├── docs/                             文档与本工作日志
├── scripts/                          validate_plugin.py, generate_const.py 等脚本
└── tests/                            单元测试（已适配 bagel_mac）
```

- 上游原版保留在 `plugins/zzz-bagel-plugin/`，ID 为 `bagel`，只读参考。
- 本地 macOS 分支位于 `plugins/zzz_bagel_plugin_bmthang94/`，ID 为 `bagel_mac`，可独立更新、独立保存配置，互不覆盖。

## macOS PlayCover 适配修改项

1. **Windows 专有 API 保护**：
   - `bagel_mac/operation/operation.py`: `scroll_interaction_row` 与 `release_flow_inputs` 中的 `ctypes.windll` 调用加上 `hasattr(ctypes, 'windll')` 保护，macOS 下使用 `pyautogui.scroll` 兜底。
   - `bagel_mac/route/route_recorder.py`: `is_foreground` 中的 `GetForegroundWindow` 调用加上 `hasattr(ctypes, 'windll')` 保护。
2. **后台模式保护**：
   - macOS PlayCover 无 Windows 后台手柄模式，`controller.background_mode` 读取统一走 `getattr(controller, 'background_mode', False)`。
   - 主仓 `PcControllerBase` 也补上了默认 `@property def background_mode(self) -> bool: return False`。
3. **按键映射兼容性**：
   - 主仓 `GameConfig.get_action_keys` 增加了默认参数 `control_method: str = 'keyboard'`，兼容插件显式传参。
4. **独立 ID 与配置**：
   - 插件 ID 改为 `bagel_mac`，所有 10 份 `screen_info/*.yml` 中 `app_id` 同步改为 `bagel_mac`。
   - 配置文件：`config/<实例>/one_dragon/bagel_mac.yml`。
   - 运行记录：`config/<实例>/app_run_record/bagel_mac.yml`。
5. **单元测试通过**：
   - `tests/` 中的导入已同步改为 `bagel_mac`，全部 79 项单元测试通过。
   - `validate_plugin.py --repo . --strict` 严格校验通过。

## 2026-11-26 PlayCover 画面区域逐屏重新标定

`bagel_mac/screen_info/*.yml` 的 `pc_rect` 按 Windows 16:9 布局标定。PlayCover 窗口约 2:1、被拉伸成 1920x1080，按钮和标题的位置与 Windows 不同，多数偏移在纵向。办法是逐个画面用实机帧跑一遍插件自己的 OCR，按实测文字框加边距重设矩形；没有 PlayCover 实机帧的画面不改。

| 画面 | 状态 | 依据 |
| --- | --- | --- |
| 贝果-选图（`bagel_map.yml`） | 已重标：前往备战、选中地图、推荐价值、三个页签；雅努斯、城郊幻境两张地图卡原矩形已能读到，未改 | `docs/materials/screens/playcover_map_select.webp`，`tests/test_map_select_playcover.py` |
| 贝果计划-主页面（`bagel_hub.yml`） | 标题、前往空洞、仓储枢纽、商店在实机帧上读得到，未改；接待员名称、对话人、出发对话没有实机帧，未核对 | 运行时留存帧（主页面一张，未入库） |
| 其余（备战、入场确认、仓库、局内、结算、商店、退出确认、拟态提示） | 无 PlayCover 帧，未核对 | — |

已知的 Windows 到 PlayCover 偏移（以矩形中心对文字中心，仅 4 组数据，不能当通用变换）：页面上半部元素上移约 30 至 40 px，下半部元素下移约 37 至 40 px，相当于以 y=540 为中心纵向放大约 1.07 至 1.13 倍；横向偏移各处不一致。

## 2026-11-26 英文界面（i18n）

插件界面、日志、路线编辑器跟随一条龙语言设置（`ui_language`），新增英文支持；中文与其他语言保持原文。

- 新增 `bagel_mac/i18n.py`：`tr()`（显示文本）、`tr_status()`（仅显示状态/停止原因，精确 + `{占位符}` 模板匹配）、`web_bootstrap_script()`（词典注入路线编辑器网页的 `<!--BAGEL_I18N-->` 占位符，页面内 `t()`/`fmt()`/`data-i18n*` 消费）。
- 新增词典 `bagel_mac/assets/i18n/en.yml`（约 1350 条，键为中文原文）；`web/index.html` 静态文案全部挂 `data-i18n*`，动态文案走 `t()`。
- 范围覆盖设置页与各弹窗、启动校验与配置校验错误、浮层与线程状态、`app.py`/`operation/`/`route/`/`vision/` 全部 `log.*` 与 `raise` 文本。
- 决策：应用名在英文下显示 `Operation Bagel (macOS)`（`plugin.json` 的 `name`/`summary` 保持中文，商店卡片不翻译）；`display_name` 英文为 `Operation [ … ]`；术语优先用官方英文名（Janus Illusion、Gear Coins、Keycard 等），无官方名的保留自拟直译并登记在 `docs/README.md` 术语表；状态/`STATUS_*`/`round_*` 返回值、配置值、OCR 目标、用户路线名一律保持中文，仅在显示时翻译。
- 验证：`pytest tests/` 121 项通过（`tests/test_i18n.py` 覆盖词典结构、日志/状态扫描守卫、tr_status、界面快照与网页守卫）；`validate_plugin.py --repo . --strict` 通过；zh 下设置页与悬浮窗文本与改版前快照逐字节一致。
- 实机界面检查（英文运行一遍设置页、路线编辑器、日志输出）尚未进行，待下次连机验收。
