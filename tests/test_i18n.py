"""贝果插件翻译词典的结构检查：tr 行为、字面量覆盖、占位符对齐与词典质量。

词典是 ``bagel_mac/assets/i18n/en.yml``，键为代码里的中文原文。
本文件里的扫描辅助函数在后续阶段继续扩展（设置页、网页、日志）。

运行：`PYTHONPATH=<主仓>/src uv run pytest <本仓库>/tests/`。
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest
import yaml
from one_dragon.utils import i18_utils

from bagel_mac import i18n

REPO_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_DIR / 'bagel_mac'
EN_CATALOG_PATH = PLUGIN_DIR / 'assets' / 'i18n' / 'en.yml'
INDEX_HTML_PATH = PLUGIN_DIR / 'web' / 'index.html'

_MODULE_FILES = sorted(PLUGIN_DIR.rglob('*.py'))

_CJK_RE = re.compile(r'[぀-ヿ一-鿿가-힯]')
_FORMAT_FIELD_RE = re.compile(r'\{([^{}!:\]]*)')
_PERCENT_RE = re.compile(r'%(?!%)(?:\([^)]+\))?[-+#0 ]*\d*(?:\.\d+)?([sdr])')
_LOG_PERCENT_RE = re.compile(r'%(?!%)[-+#0 ]*\d*(?:\.\d+)?[sdrf]')


class _NoDupLoader(yaml.SafeLoader):
    """遇到重复键直接报错的加载器。"""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=True)
            if key in mapping:
                raise ValueError(f'词典重复键：{key}')
            mapping[key] = self.construct_object(value_node, deep=True)
        return mapping


def load_catalog(path: Path = EN_CATALOG_PATH) -> dict[str, str]:
    """读取词典文件；重复键、非字典内容都会抛错。"""
    data = yaml.load(path.read_text(encoding='utf-8'), Loader=_NoDupLoader)
    assert isinstance(data, dict), f'{path} 顶层必须是扁平字典'
    return {str(k): str(v) for k, v in data.items()}


NAME_SEPARATORS = re.compile(r'-|_| \+ ')

GENERATED_NAME_PARTS = frozenset({'自定义', '录制', '房间段录制', '副本', '默认'})
"""网页新建路线时拼进默认名的后缀：属于数据，只在显示时翻译。"""

EXTRA_NAME_PARTS = frozenset({'无力野人镇', '汽车旅馆', '仓储中心'})
"""游戏里房间全名的区域前缀与建筑名：内置数据只用整串或更长的组合，
但用户自录路线常以它们命名，所以单独收录。"""


def data_names() -> set[str]:
    """内置数据里出现的全部出生点、房间、房间入口与内置路线名（中文原名）。"""
    game_data = PLUGIN_DIR / 'assets' / 'game_data' / 'bagel'
    names: set[str] = set()
    spawns = yaml.safe_load((game_data / 'spawn_points.yml').read_text(encoding='utf-8'))
    for item in spawns.get('spawn_points', spawns.get('spawns', [])):
        names.add(item['name'])
    rooms = yaml.safe_load((game_data / 'keycard_rooms.yml').read_text(encoding='utf-8'))
    for room in rooms['rooms']:
        names.add(room['name'])
        names.add(room['name'] + '入口')
    for path in (game_data / 'routes').glob('*.route.json'):
        names.add(json.loads(path.read_text(encoding='utf-8'))['name'])
    return names


def data_name_part_keys() -> set[str]:
    """内置名字按 tr_name 的规则拆出来的各部分；不含分隔符的名字，整串就是一部分。"""
    parts: set[str] = set()
    for name in data_names():
        parts.update(part for part in NAME_SEPARATORS.split(name) if part)
    return parts


def _tr_call_args() -> tuple[set[str], list[str]]:
    """扫描插件全部源码里的 ``tr(...)`` 调用。

    Returns:
        (字面量集合, 违规列表)。违规项为 ``file:line 原因``。
        首参只允许：字符串字面量（相邻隐式拼接算一个）、
        或裸名字 / 属性 / 下标（动态展示常量）；其余一律违规。
    """
    literals: set[str] = set()
    violations: list[str] = []
    for path in _MODULE_FILES:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_tr = (isinstance(func, ast.Name) and func.id in ('tr', 'tr_n')) or (
                isinstance(func, ast.Attribute) and func.attr in ('tr', 'tr_n')
            )
            if not is_tr or not node.args:
                continue
            arg = node.args[0]
            loc = f'{path.relative_to(REPO_DIR)}:{node.lineno}'
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literals.add(arg.value)
            elif isinstance(arg, (ast.Name, ast.Attribute, ast.Subscript)):
                pass  # 动态展示常量，字面量由别处定义，允许
            else:
                kind = type(arg).__name__
                violations.append(f'{loc} tr() 首参必须是字面量或常量引用，收到 {kind}')
    return literals, violations


def _plural_call_literals() -> set[str]:
    """插件源码里 ``tr_n(...)`` 首参的字面量。"""
    found: set[str] = set()
    for path in _MODULE_FILES:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == 'tr_n'
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                found.add(node.args[0].value)
    return found


def _page_plural_keys(html: str) -> set[str]:
    """网页里 ``tn('...', n)`` 的键。"""
    return {plural for _, _, plural in _T_CALL_RE.findall(html) if plural}


def _all_str_constants() -> set[str]:
    """插件全部源码里的字符串字面量集合（含 docstring 与拼接后的整体）。"""
    found: set[str] = set()
    for path in _MODULE_FILES:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return found


def missing_catalog_entries(literals: set[str], catalog: dict[str, str]) -> list[str]:
    """找出没有词典条目的 tr 字面量。"""
    return sorted(
        text for text in literals
        if not catalog.get(text)
    )


# 页面里的 t('...') / t("...") 字面量；要求 t 前面不是标识符字符，
# 免得误抓 createElement('div')、split('...') 这类调用。
_T_CALL_RE = re.compile(
    r'''(?<![A-Za-z0-9_$])t\('((?:[^'\\]|\\.)*)'\)'''
    r'''|(?<![A-Za-z0-9_$])t\("((?:[^"\\]|\\.)*)"\)'''
    # tn('原文', n)：按数量选单复数，键是复数形式的原文
    r'''|(?<![A-Za-z0-9_$])tn\('((?:[^'\\]|\\.)*)','''
)
_DATA_I18N_RE = re.compile(r'data-i18n(?:-title|-placeholder)?="([^"]*)"')

# 页面里允许残留中文的位置：注释、data-i18n* 属性、t()/fmt() 的字面量、
# 以及用户数据（地图标识、路线名后缀）——这些字符串是数据不是界面文案。
_HTML_ALLOWED_CJK = (
    # 地图标识是数据键，参与 state.maps 查找，不翻译。
    '雅努斯幻境',
    # 路线名的生成后缀写进用户路线名里，属于数据，不翻译。
    '-房间段', '-自定义', '-房间段录制', '-录制', '-副本', '-默认',
)


def page_i18n_keys(html: str) -> set[str]:
    """收集 index.html 里 data-i18n* 属性值与 t() 字面量。"""
    keys = set(_DATA_I18N_RE.findall(html))
    for single, double, plural in _T_CALL_RE.findall(html):
        raw = single or double or plural
        keys.add(raw.replace("\\'", "'").replace('\\"', '"').replace('\\\\', '\\'))
    return keys


def stray_cjk_in_html(html: str, allowed: tuple[str, ...] = _HTML_ALLOWED_CJK) -> list[str]:
    """剥掉注释、data-i18n* 属性、t()/fmt() 参数与允许残留项后，报告残留中文行。"""
    # 去掉 // 行注释、/* */ 块注释、<!-- --> 注释
    stripped = re.sub(r'/\*.*?\*/', '', html, flags=re.S)
    stripped = re.sub(r'<!--.*?-->', '', stripped, flags=re.S)
    stripped = re.sub(r'//[^\n]*', '', stripped)
    # data-i18n* 属性的值，以及配套保留中文的 title / placeholder 属性值
    stripped = _DATA_I18N_RE.sub('""', stripped)
    stripped = re.sub(r'\b(?:title|placeholder)="[^"]*"', '""', stripped)
    # t('...') / t("...") / fmt(t('...')) 的键字面量
    stripped = _T_CALL_RE.sub('t()', stripped)
    # 带 data-i18n 的元素仍保留中文文本节点（zh 原始渲染），剥掉这些键本身
    data_keys = set(_DATA_I18N_RE.findall(html))
    sorted_allowed = sorted(allowed, key=len, reverse=True)
    problems = []
    for lineno, line in enumerate(stripped.split('\n'), 1):
        code = line
        for key in data_keys:
            code = code.replace(key, '')
        for item in sorted_allowed:
            code = code.replace(item, '')
        if _CJK_RE.search(code):
            problems.append(f'{lineno}: {line.strip()[:120]}')
    return problems


_LOG_CALL_NAMES = ('log', 'print')
# 把状态文本交给 round_* 的方法：键是方法名，值是状态文本所在的位置参数下标。
# 除 round_* 外还有各操作里转发状态的小方法；新增这类方法时在这里登记，
# 状态守卫才会检查它们的参数有没有收进词典。
STATUS_HELPERS: dict[str, int] = {
    'round_fail': 0, 'round_retry': 0, 'round_success': 0, 'round_wait': 0,
    'round_recoverable_fail': 0,
    'transfer_fail': 0, 'read_again': 0, 'wait_transfer': 0, '_wait_unload': 0,
    '_fail': 0, '_pages_done': 0, '_give_up': 0, '_wait_panel': 0,
    '_skip_source': 1, '_skip_and_reselect': 1,
    '_finish_fail': 0, '_fail_after_survey': 0, '_blocked': 0, '_start_card_probe': 0,
    '_end_pickup_fast': 0, '_give_up_pickup': 1, 'interact_container': 0,
    '_finish_failed_round': 1,
}


def find_untranslated_log_calls(paths: Iterable[Path]) -> list[str]:
    """扫描 log.X(...) / print(...)：首参是含中文的字面量且没被 tr() 包住即违规。"""
    problems: list[str] = []
    for path in paths:
        src = path.read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_log = (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id in _LOG_CALL_NAMES
            ) or (isinstance(func, ast.Name) and func.id in _LOG_CALL_NAMES)
            if not is_log or not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if _CJK_RE.search(first.value):
                    problems.append(f'{path}:{first.lineno}')
            elif isinstance(first, ast.JoinedStr):
                segment = ast.get_source_segment(src, first) or ''
                if _CJK_RE.search(segment):
                    problems.append(f'{path}:{first.lineno}')
    return problems


def _round_text_arg(node: ast.Call, index: int = 0) -> ast.expr | None:
    """状态方法里装状态文本的位置参数；缺这个参数时返回 None。"""
    return node.args[index] if len(node.args) > index else None


def _template_literal(node: ast.expr) -> ast.Constant | None:
    """`'模板'.format(...)` 调用里的模板字面量。"""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == 'format'
        and isinstance(node.func.value, ast.Constant)
        and isinstance(node.func.value.value, str)
    ):
        return node.func.value
    return None


def find_status_texts(paths: Iterable[Path]) -> set[str]:
    """收集传给 round_fail/round_retry/round_success/round_wait 的中文字面量与模板。"""
    texts: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_round = isinstance(func, ast.Attribute) and func.attr in STATUS_HELPERS
            if not is_round:
                continue
            arg = _round_text_arg(node, STATUS_HELPERS[func.attr])
            if arg is None:
                continue
            for candidate in (arg.body, arg.orelse) if isinstance(arg, ast.IfExp) else (arg,):
                if (
                    isinstance(candidate, ast.Constant)
                    and isinstance(candidate.value, str)
                    and _CJK_RE.search(candidate.value)
                ):
                    texts.add(candidate.value)
                else:
                    template = _template_literal(candidate)
                    if template is not None and _CJK_RE.search(template.value):
                        texts.add(template.value)
    return texts


def find_fstring_statuses(paths: Iterable[Path]) -> list[str]:
    """状态方法的状态文本参数里不允许直接写含中文的 f-string。

    f-string 拼出来的整句没法进词典：写成 ``'模板 {v0}'.format(v0=...)``，
    词典里登记同样的模板，``tr_status`` 才能按占位符匹配。
    """
    problems: list[str] = []
    for path in paths:
        src = path.read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in STATUS_HELPERS):
                continue
            arg = _round_text_arg(node, STATUS_HELPERS[func.attr])
            if isinstance(arg, ast.JoinedStr) and _CJK_RE.search(ast.get_source_segment(src, arg) or ''):
                problems.append(f'{path}:{arg.lineno}')
    return problems


def status_attr_values(paths: Iterable[Path]) -> set[str]:
    """收集 STATUS_* / OUTCOME_* 这类类属性与模块常量的中文值。"""
    texts: set[str] = set()
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            if isinstance(node, ast.AnnAssign) and isinstance(node.value, ast.Constant):
                target = node.target
                name = target.attr if isinstance(target, ast.Attribute) else (
                    target.id if isinstance(target, ast.Name) else ''
                )
                if (
                    name.startswith(('STATUS_', 'OUTCOME_', '_STATUS_'))
                    and isinstance(node.value.value, str)
                    and _CJK_RE.search(node.value.value)
                ):
                    texts.add(node.value.value)
    return texts


# 永远不会显示给用户的状态文本及其原因（其余状态都可能经摘要/通知/悬浮窗展示）。
# 当前为空：插件的状态都会经 result.status 走到 stats.summary 的停止原因、
# 失败通知或路线试跑悬浮窗，所以全部收进 en.yml。
NEVER_DISPLAYED_STATUSES: dict[str, str] = {}


def _stray_cjk_runs(zh: str, en: str) -> list[str]:
    """译文里不允许出现键中没有的中日韩文本（如路线名、角色名允许透传）。"""
    runs = re.findall(r'[぀-ヿ一-鿿가-힯]+', en)
    return [run for run in runs if run not in zh]


ONE_SUFFIX = i18n.ONE_SUFFIX


def base_key(key: str) -> str:
    """``<原文>|one`` 单数条目的基础键；普通键原样返回。"""
    return key[: -len(ONE_SUFFIX)] if key.endswith(ONE_SUFFIX) else key


def orphan_singular_keys(catalog: dict[str, str]) -> list[str]:
    """找出没有基础键的 ``|one`` 单数条目。"""
    return sorted(
        key for key in catalog
        if key.endswith(ONE_SUFFIX) and base_key(key) not in catalog
    )


def placeholder_mismatches(catalog: dict[str, str]) -> list[str]:
    """检查每条词典的占位符两侧一致：{...} 字段名集合、%s/%d/%r 计数。

    ``|one`` 单数条目与它的基础键用同一组占位符，所以拿基础键当中文侧比较。
    """
    problems: list[str] = []
    for key, en in catalog.items():
        zh = base_key(key)
        zh_fields = sorted(_FORMAT_FIELD_RE.findall(zh))
        en_fields = sorted(_FORMAT_FIELD_RE.findall(en))
        if zh_fields != en_fields:
            problems.append(f'{{}} 字段不一致 {zh!r}: {zh_fields} != {en_fields}')
        zh_pct = sorted(_PERCENT_RE.findall(zh))
        en_pct = sorted(_PERCENT_RE.findall(en))
        if zh_pct != en_pct:
            problems.append(f'%% 占位符不一致 {zh!r}: {zh_pct} != {en_pct}')
    return problems


@pytest.fixture(scope='module', autouse=True)
def _plugin_on_sys_path() -> Iterator[None]:
    """插件以仓库根为模块根，导入前先把它放进 sys.path。"""
    entry = str(REPO_DIR)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    yield


@pytest.fixture
def lang() -> Iterator[Callable[[str], None]]:
    """切换主程序默认语言，结束后恢复原语言并清空插件词典缓存。"""
    from bagel_mac import i18n

    original = i18_utils.get_default_lang()

    def _set(code: str) -> None:
        i18_utils.update_default_lang(code)
        i18n.clear_cache()

    try:
        yield _set
    finally:
        i18_utils.update_default_lang(original)
        i18n.clear_cache()


class TestTr:
    """tr 的查找顺序：中文返回原文；否则插件词典 → 主程序 gt → 原文。"""

    def test_zh_returns_source(self, lang) -> None:
        from bagel_mac import i18n

        lang('zh')
        assert i18n.tr('雅努斯幻境') == '雅努斯幻境'
        assert i18n.tr('') == ''

    def test_zh_prefix_returns_source(self, lang) -> None:
        from bagel_mac import i18n

        lang('zh-CN')
        assert i18n.tr('雅努斯幻境') == '雅努斯幻境'

    def test_en_uses_catalog(self, lang) -> None:
        from bagel_mac import const, i18n

        lang('en')
        assert i18n.tr('雅努斯幻境') == 'Janus Illusion'
        assert i18n.tr(const.APP_NAME) == 'Operation Bagel (macOS)'

    def test_en_falls_back_to_gt(self, lang, monkeypatch: pytest.MonkeyPatch) -> None:
        from bagel_mac import i18n

        lang('en')
        monkeypatch.setattr(i18n, 'gt', lambda msg, model='ui', lang=None: f'GT:{msg}')
        assert i18n.tr('词典里没有的句子') == 'GT:词典里没有的句子'

    def test_en_falls_back_to_source(self, lang) -> None:
        from bagel_mac import i18n

        lang('en')
        # 词典和主程序 mo 都没有的文本，保持原文。
        assert i18n.tr('词典里没有的句子XYZ') == '词典里没有的句子XYZ'

    def test_unknown_lang_returns_source(self, lang) -> None:
        from bagel_mac import i18n

        lang('ja')  # 没有 ja.yml，回退原文（gt 也可能命中，但不得报错）
        result = i18n.tr('雅努斯幻境')
        assert isinstance(result, str) and len(result) > 0

    def test_web_catalog(self, lang) -> None:
        from bagel_mac import i18n

        lang('zh')
        assert i18n.web_catalog() == {}
        lang('en')
        catalog = i18n.web_catalog()
        assert catalog == load_catalog()
        assert catalog['城郊幻境'] == 'Suburbs Illusion'


class TestCatalogStructure:
    """词典与代码字面量的一致性检查。"""

    def test_tr_call_arguments_are_static(self) -> None:
        _, violations = _tr_call_args()
        assert violations == []

    def test_tr_results_are_only_formatted(self) -> None:
        """tr() 的返回值只能接 ``.format`` / ``.join``：``log.info(tr('...').name)`` 这类笔误会在运行时才抛错。"""
        problems: list[str] = []
        for path in _MODULE_FILES:
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr not in ('format', 'join')
                    and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Name)
                    and node.value.func.id in ('tr', 'tr_n', 'tr_name', 'tr_status')
                ):
                    problems.append(f'{path.relative_to(REPO_DIR)}:{node.lineno} tr(...).{node.attr}')
        assert problems == []

    def test_log_template_placeholder_count_matches_arguments(self) -> None:
        """``log.x(tr('...%s...'), a, b)``：占位符个数要和后面的参数个数一致，否则日志会在打印时报错。"""
        problems: list[str] = []
        for path in _MODULE_FILES:
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'log'
                    and node.args
                ):
                    continue
                first = node.args[0]
                if not (
                    isinstance(first, ast.Call)
                    and isinstance(first.func, ast.Name)
                    and first.func.id in ('tr', 'tr_n')
                    and first.args
                    and isinstance(first.args[0], ast.Constant)
                    and isinstance(first.args[0].value, str)
                ) or any(isinstance(arg, ast.Starred) for arg in node.args):
                    continue
                expected = len(_LOG_PERCENT_RE.findall(first.args[0].value))
                if expected != len(node.args) - 1:
                    problems.append(f'{path.relative_to(REPO_DIR)}:{node.lineno} 占位符 {expected} 个，参数 {len(node.args) - 1} 个')
        assert problems == []

    def test_every_tr_literal_has_entry(self) -> None:
        literals, _ = _tr_call_args()
        catalog = load_catalog()
        assert missing_catalog_entries(literals, catalog) == []

    def test_every_page_key_has_entry(self) -> None:
        """index.html 的 data-i18n* 属性与 t() 字面量都要有词典条目。"""
        keys = page_i18n_keys(INDEX_HTML_PATH.read_text(encoding='utf-8'))
        catalog = load_catalog()
        assert missing_catalog_entries(keys, catalog) == []

    def test_no_unused_keys(self) -> None:
        literals, _ = _tr_call_args()
        constants = _all_str_constants()
        page_keys = page_i18n_keys(INDEX_HTML_PATH.read_text(encoding='utf-8'))
        name_keys = data_name_part_keys() | GENERATED_NAME_PARTS | EXTRA_NAME_PARTS
        catalog = load_catalog()
        unused = sorted(
            key for key in catalog
            if base_key(key) not in literals and base_key(key) not in constants
            and base_key(key) not in page_keys and key not in name_keys
        )
        assert unused == []

    def test_singular_keys_have_a_base_key(self) -> None:
        """``|one`` 单数条目必须有基础键，否则单数永远不会被用到。"""
        assert orphan_singular_keys(load_catalog()) == []

    def test_singular_keys_are_used_through_plural_seam(self) -> None:
        """每个 ``|one`` 的基础键都要经 tr_n / tn 取用，免得写了单数却没接上。"""
        catalog = load_catalog()
        plural_literals = _plural_call_literals() | _page_plural_keys(
            INDEX_HTML_PATH.read_text(encoding='utf-8')
        )
        unused = sorted(
            key for key in catalog
            if key.endswith(ONE_SUFFIX) and base_key(key) not in plural_literals
        )
        assert unused == []

    def test_placeholder_parity(self) -> None:
        assert placeholder_mismatches(load_catalog()) == []

    def test_values_are_english(self) -> None:
        catalog = load_catalog()
        bad = {
            k: v for k, v in catalog.items()
            if not v.strip() or _stray_cjk_runs(k, v)
        }
        assert bad == {}

    def test_catalog_has_no_duplicate_keys(self) -> None:
        # load_catalog 内部用的是遇重复键即报错的加载器，能读出来即无重复。
        assert load_catalog()


class TestWebPage:
    """路线编辑器网页的注入与残留中文检查。"""

    def test_exactly_one_i18n_placeholder(self) -> None:
        html = INDEX_HTML_PATH.read_text(encoding='utf-8')
        assert html.count('<!--BAGEL_I18N-->') == 1

    def test_no_stray_cjk_in_html(self) -> None:
        """静态标记不允许留中文：要么 data-i18n*，要么 t()/fmt()，要么是数据。"""
        problems = stray_cjk_in_html(INDEX_HTML_PATH.read_text(encoding='utf-8'))
        assert problems == []

    def test_bootstrap_script_en(self, lang) -> None:
        """注入脚本：JSON 可解析、就是整份词典、值里没有裸 </ 截断 script。"""
        import json

        from bagel_mac import i18n

        lang('en')
        script = i18n.web_bootstrap_script()
        match = re.search(r'window\.BAGEL_I18N=(\{.*\});</script>$', script, re.S)
        assert match, script[:120]
        assert json.loads(match.group(1)) == load_catalog()
        assert 'window.BAGEL_LANG="en"' in script

    def test_bootstrap_script_zh_empty(self, lang) -> None:
        import json

        from bagel_mac import i18n

        lang('zh')
        script = i18n.web_bootstrap_script()
        match = re.search(r'window\.BAGEL_I18N=(\{.*\});</script>$', script, re.S)
        assert match and json.loads(match.group(1)) == {}

    def test_bootstrap_script_escapes_close_tag(self, lang, monkeypatch: pytest.MonkeyPatch) -> None:
        """词典值含 </script> 时也要转义，不能截断注入块。"""
        import json

        from bagel_mac import i18n

        lang('en')
        monkeypatch.setattr(i18n, 'web_catalog', lambda: {'坏值': 'x</script>y'})
        script = i18n.web_bootstrap_script()
        payload = script.split('window.BAGEL_I18N=', 1)[1].rsplit(';</script>', 1)[0]
        assert '</' not in payload
        assert json.loads(payload) == {'坏值': 'x</script>y'}


class TestOverlayTexts:
    """悬浮窗与自绘文字：en 下无中文，zh 下与改版前逐字一致。"""

    @staticmethod
    def _display_strings() -> dict[str, object]:
        pytest.importorskip('PySide6')
        pytest.importorskip('qfluentwidgets')
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from bagel_mac.gui import route_web_editor
        from bagel_mac.gui.record_overlay import MARKER_ITEMS, BagelRecordOverlay
        from bagel_mac.gui.run_overlay import KIND_LABELS, BagelRunOverlay

        return {
            'record_title': BagelRecordOverlay().windowTitle(),
            'run_title': BagelRunOverlay().windowTitle(),
            'marker_items': [[key, name, kind] for key, name, kind in MARKER_ITEMS],
            'run_kind_labels': dict(KIND_LABELS),
            'undo_key_label': route_web_editor.UNDO_KEY_LABEL,
        }

    def test_en_overlay_texts(self, lang) -> None:
        """悬浮窗标题与自绘文字（经 tr()）在 en 下不含中文。"""
        from bagel_mac import i18n

        lang('en')
        strings = self._display_strings()
        bad = []
        for text in (strings['record_title'], strings['run_title'], strings['undo_key_label']):
            if _CJK_RE.search(str(i18n.tr(text))):
                bad.append(text)
        for key, name, _kind in strings['marker_items']:
            for text in (key, name):
                if _CJK_RE.search(i18n.tr(text)):
                    bad.append(text)
        for label in strings['run_kind_labels'].values():
            if _CJK_RE.search(i18n.tr(label)):
                bad.append(label)
        assert bad == []

    def test_zh_overlay_texts_snapshot(self, lang) -> None:
        """zh 下与改版前快照一致（tests/zh_overlay_texts.json）。"""
        import json

        lang('zh')
        strings = self._display_strings()
        snapshot = json.loads(
            (REPO_DIR / 'tests' / 'zh_overlay_texts.json').read_text(encoding='utf-8')
        )
        assert strings == snapshot


class TestHelperNegativeProof:
    """扫描辅助函数本身要能查出问题，不能恒过。"""

    def test_missing_entries_detects_gap(self) -> None:
        literals = {'有译文', '没译文'}
        catalog = {'有译文': 'Translated'}
        assert missing_catalog_entries(literals, catalog) == ['没译文']

    def test_placeholder_mismatch_detects_brace(self) -> None:
        catalog = {'房间 {name}': 'Room {room}'}
        assert placeholder_mismatches(catalog) != []

    def test_placeholder_mismatch_detects_percent(self) -> None:
        catalog = {'获得 %s 枚': 'Got %d coins'}
        assert placeholder_mismatches(catalog) != []


class TestDisplayMessages:
    """路线校验与配置消息：en 走词典，zh 与改前逐字一致。"""

    def test_route_name_required(self, lang) -> None:
        from bagel_mac.route.route_model import validate_route_name

        lang('en')
        with pytest.raises(ValueError, match='Route name cannot be empty'):
            validate_route_name('   ')
        lang('zh')
        with pytest.raises(ValueError, match='路线名称不能为空'):
            validate_route_name('   ')

    def test_route_start_first_point_kind(self, lang) -> None:
        """{kind} 占位符在 en 下填 KIND_LABELS 的英文展示副本。"""
        from bagel_mac.route.route_model import (
            BagelRoute,
            BagelRoutePoint,
            BagelSpawnPoint,
            check_route_start,
        )

        spawn = BagelSpawnPoint('sp1', '录像店', '雅努斯幻境/Custom', 0.0, 0.0, 10.0)
        route = BagelRoute('r1', 'route', 'sp1')
        route.points.append(BagelRoutePoint(0.0, 0.0, '雅努斯幻境/Custom', 'door'))
        lang('en')
        assert check_route_start(route, spawn) == 'The first point must be a waypoint; it is Door'
        lang('zh')
        assert check_route_start(route, spawn) == '首点必须是途径点，当前是门'

    def test_route_start_spawn_name_translated_for_display(self, lang) -> None:
        """内置出生点名在 en 下显示英文名；用户自起的名字原样透传。"""
        from bagel_mac.route.route_model import (
            BagelRoute,
            BagelRoutePoint,
            BagelSpawnPoint,
            check_route_start,
        )

        spawn = BagelSpawnPoint('sp1', '录像店', '雅努斯幻境/Custom', 0.0, 0.0, 10.0)
        route = BagelRoute('r1', 'route', 'sp1')
        route.points.append(BagelRoutePoint(0.0, 0.0, '雅努斯幻境/另一层', 'beacon'))
        lang('en')
        assert check_route_start(route, spawn) == (
            'First point layer 雅努斯幻境/另一层 does not match the Random Play layer 雅努斯幻境/Custom'
        )
        lang('zh')
        assert check_route_start(route, spawn) == '首点分层 雅努斯幻境/另一层 与录像店分层 雅努斯幻境/Custom 不一致'


class TestOffscreenUi:
    """离屏构建设置页：en 下无中文残留，zh 下与改版前文本集逐字一致。"""

    @staticmethod
    def _collect_texts(root) -> set[tuple[str, str]]:
        """遍历控件树，收集 text/toolTip/accessibleName/windowTitle/placeholderText 与下拉项。"""
        from PySide6.QtWidgets import QComboBox, QWidget

        found: set[tuple[str, str]] = set()
        stack = [root]
        while stack:
            widget = stack.pop()
            for kind in ('text', 'toolTip', 'accessibleName', 'windowTitle', 'placeholderText'):
                getter = getattr(widget, kind, None)
                if getter is None:
                    continue
                try:
                    value = getter()
                except Exception:  # noqa: BLE001 - 部分控件 getter 在离屏下会抛
                    continue
                if isinstance(value, str) and value:
                    found.add((kind, value))
            if isinstance(widget, QComboBox):
                for index in range(widget.count()):
                    item = widget.itemText(index)
                    if item:
                        found.add(('itemText', item))
            stack.extend(widget.findChildren(QWidget))
        return found

    @classmethod
    def _build_settings_page(cls):
        """构建设置页内容控件；ctx 用不到时传占位对象即可。"""
        pytest.importorskip('PySide6')
        pytest.importorskip('qfluentwidgets')
        from PySide6.QtWidgets import QApplication

        QApplication.instance() or QApplication([])
        from bagel_mac.gui.setting_interface import BagelSettingInterface

        interface = BagelSettingInterface(object())
        return interface.get_content_widget()

    def test_en_has_no_cjk(self, lang) -> None:
        lang('en')
        texts = self._collect_texts(self._build_settings_page())
        bad = sorted((kind, value) for kind, value in texts if _CJK_RE.search(value))
        assert bad == []

    def test_zh_byte_identical(self, lang) -> None:
        """zh 文本集与改版前快照一致（快照存 tests/zh_settings_texts.json）。"""
        import json

        lang('zh')
        texts = self._collect_texts(self._build_settings_page())
        snapshot = {
            (kind, value)
            for kind, value in json.loads(
                (REPO_DIR / 'tests' / 'zh_settings_texts.json').read_text(encoding='utf-8')
            )
        }
        assert texts == snapshot


def _scope_files(excluded_dirs: tuple[str, ...]) -> list[Path]:
    """本次扫描范围内的插件文件（4a：排除 route/ 与 vision/，4b 再纳入）。"""
    files: list[Path] = []
    for path in _MODULE_FILES:
        rel = path.relative_to(PLUGIN_DIR)
        if len(rel.parts) > 1 and rel.parts[0] in excluded_dirs:
            continue
        files.append(path)
    return files


class TestLogCalls:
    """日志调用守卫：范围内 log.* / print 首参不得直接出现中文（须经 tr()）。"""

    _EXCLUDED: tuple[str, ...] = ()

    def test_no_raw_cjk_in_log_calls(self) -> None:
        problems = find_untranslated_log_calls(_scope_files(self._EXCLUDED))
        assert problems == []

    def test_guard_flags_bad_source(self, tmp_path: Path) -> None:
        bad = tmp_path / 'bad.py'
        bad.write_text(
            "log.info('未翻译的中文')\nlog.info(f'内联 {x} 模板')\nprint('打印中文')\n",
            encoding='utf-8',
        )
        good = tmp_path / 'good.py'
        good.write_text(
            "log.info(tr('已翻译'))\nlog.info(tr('模板 {x}').format(x=x))\n"
            "log.info(tr('懒加载 %s'), value)\n",
            encoding='utf-8',
        )
        problems = find_untranslated_log_calls([bad, good])
        assert len(problems) == 3
        assert all(p.startswith(str(bad)) for p in problems)


class TestStatusTexts:
    """状态文本守卫：round_* 传入的中文文本与 STATUS_*/OUTCOME_* 常量必须收进 en.yml
    （或登记在 NEVER_DISPLAYED_STATUSES 允许清单并写明原因）。"""

    _EXCLUDED: tuple[str, ...] = ()

    def test_round_texts_have_catalog_entries(self) -> None:
        catalog = load_catalog()
        scope = _scope_files(self._EXCLUDED)
        texts = find_status_texts(scope) | status_attr_values(scope)
        missing = sorted(
            text for text in texts
            if text not in catalog and text not in NEVER_DISPLAYED_STATUSES
        )
        assert missing == []

    def test_no_fstring_statuses(self) -> None:
        assert find_fstring_statuses(_scope_files(self._EXCLUDED)) == []

    def test_fstring_guard_flags_bad_source(self, tmp_path: Path) -> None:
        bad = tmp_path / 'bad.py'
        bad.write_text("self.round_recoverable_fail(f'失败 {x}')\nself._fail('静态')\n", encoding='utf-8')
        assert len(find_fstring_statuses([bad])) == 1

    def test_allowlist_entries_exist(self) -> None:
        scope = _scope_files(self._EXCLUDED)
        texts = find_status_texts(scope) | status_attr_values(scope)
        unknown = [text for text in NEVER_DISPLAYED_STATUSES if text not in texts]
        assert unknown == []


class TestTrStatus:
    """tr_status：精确匹配 → 模板匹配 → 原样返回；zh 恒返回原文。"""

    def test_exact_match(self, lang) -> None:
        lang('en')
        assert i18n.tr_status('仓库没有可用的该房间门禁卡') == i18n.tr('仓库没有可用的该房间门禁卡')

    def test_template_match_one_placeholder(self, lang) -> None:
        lang('en')
        out = i18n.tr_status('在商店常驻战备中未找到房卡「录像店」')
        assert out == 'keycard "Random Play" not found among permanent supplies in the shop'
        # 房卡名里没有词典条目的部分原样保留。
        out = i18n.tr_status('在商店常驻战备中未找到房卡「我的自定义房」')
        assert out == 'keycard "我的自定义房" not found among permanent supplies in the shop'

    def test_template_match_two_placeholders(self, lang) -> None:
        lang('en')
        out = i18n.tr_status('清理完毕；入仓失败：背包满')
        assert out == '清理完毕; deposit failed: 背包满'

    def test_unknown_passthrough(self, lang) -> None:
        lang('en')
        assert i18n.tr_status('未收录状态ABC123') == '未收录状态ABC123'

    def test_zh_passthrough(self, lang) -> None:
        lang('zh')
        assert i18n.tr_status('清理完毕；入仓失败：背包满') == '清理完毕；入仓失败：背包满'

    def test_walker_statuses(self, lang) -> None:
        """route_walker 的试跑状态：en 走词典/模板，zh 原样返回。"""
        lang('en')
        assert i18n.tr_status('路线走完') == 'route finished'
        assert i18n.tr_status('点选楼层：B2') == 'selecting floor: B2'
        assert i18n.tr_status('冲刺前进') == 'sprinting advance'
        assert not _CJK_RE.search(i18n.tr_status('机械保险箱已打开'))
        lang('zh')
        assert i18n.tr_status('路线走完') == '路线走完'
        assert i18n.tr_status('点选楼层：B2') == '点选楼层：B2'


class TestDisplayName:
    """display_name：zh 与框架同形 `指令[ <op_name> ]`；en 为 `Operation [ <en op name> ]`。"""

    def test_operation_display_name(self, lang) -> None:
        from bagel_mac.operation.operation import BagelOperation

        op = BagelOperation.__new__(BagelOperation)
        op.op_name = '贝果-入场'
        lang('zh')
        assert op.display_name == '指令[ 贝果-入场 ]'
        lang('en')
        assert op.display_name == 'Operation [ Bagel-entry ]'
        assert not _CJK_RE.search(op.display_name)


class TestSummaryTexts:
    """stats.summary：en 无中文；zh 与原文逐字节一致。"""

    @staticmethod
    def _stats():
        from bagel_mac.stats import BagelRunStats

        stats = BagelRunStats()
        stats.finished_rounds = 4
        stats.outcome_counts = {'成功入仓': 3, '失败局': 1}
        stats.total_income = 12345
        stats.income_unknown_rounds = 2
        stats.red_totals = {'电子保险箱': 1}
        return stats

    def test_summary_no_cjk_under_en(self, lang) -> None:
        lang('en')
        text = self._stats().summary('仓库没有可用的该房间门禁卡')
        assert text is not None
        assert not _CJK_RE.search(text)

    def test_summary_zh_unchanged(self, lang) -> None:
        lang('zh')
        text = self._stats().summary('仓库没有可用的该房间门禁卡')
        assert '已停止：仓库没有可用的该房间门禁卡' in text
        assert '成功 3 局 · 失败 1 局' in text
        assert '红色物品：电子保险箱 ×1' in text


class TestPlural:
    """tr_n：英文数量为 1 时用 ``|one`` 单数条目；中文和复数不变。"""

    def test_one_uses_singular_entry(self, lang) -> None:
        lang('en')
        assert i18n.tr_n(' 条路线 ', 1) == ' route '
        assert i18n.tr_n(' 条路线 ', 2) == ' routes '
        assert i18n.tr_n(' 条路线 ', 0) == ' routes '

    def test_falls_back_to_tr_without_singular_entry(self, lang) -> None:
        lang('en')
        assert i18n.tr_n('雅努斯幻境', 1) == i18n.tr('雅努斯幻境') == 'Janus Illusion'

    def test_zh_never_changes(self, lang) -> None:
        lang('zh')
        assert i18n.tr_n(' 条路线 ', 1) == ' 条路线 '
        assert i18n.tr_n(' 条路线 ', 5) == ' 条路线 '

    def test_formatted_messages(self, lang) -> None:
        lang('en')
        one = i18n.tr_n('已保存「{name}」· {count} 点', 1).format(name='x', count=1)
        many = i18n.tr_n('已保存「{name}」· {count} 点', 3).format(name='x', count=3)
        assert one == 'Saved "x" · 1 point'
        assert many == 'Saved "x" · 3 points'
        lang('zh')
        assert i18n.tr_n('已保存「{name}」· {count} 点', 1).format(name='x', count=1) == '已保存「x」· 1 点'

    def test_singular_without_base_key_is_flagged(self) -> None:
        assert orphan_singular_keys({'a|one': 'x', 'b': 'y', 'b|one': 'z'}) == ['a|one']

    def test_singular_placeholders_follow_base_key(self) -> None:
        assert placeholder_mismatches({'{n} 条|one': '{n} item', '{n} 条': '{n} items'}) == []
        assert placeholder_mismatches({'{n} 条|one': '{m} item'}) != []

    def test_page_plural_sites_use_tn(self) -> None:
        """网页里的数量文字走 tn()，不再是 t(' 条路线 ') 加数字拼接。"""
        html = INDEX_HTML_PATH.read_text(encoding='utf-8')
        for key in (' 条房间段 ', ' 条路线 ', ' 个节点', '{n} 条路线', '{n} 个出生点', '{n} 个房间 · '):
            assert f"t('{key}')" not in html, key
            assert key in _page_plural_keys(html), key


class TestNames:
    """tr_name：出生点、房间、内置路线名只在显示时翻译。"""

    def test_exact_match_first(self, lang) -> None:
        lang('en')
        assert i18n.tr_name('录像店') == 'Random Play'
        assert i18n.tr_name('无力野人镇芝邦邦餐厅') == 'Bunless Bae Town Cheezburg Bistro'

    def test_split_on_separators_and_rejoin(self, lang) -> None:
        lang('en')
        assert i18n.tr_name('录像店-非高危') == 'Random Play-Non-High-Risk'
        assert i18n.tr_name('风蚀公路_低-汽车旅馆二层-入口段-默认') == (
            'Windworn Highway_Low-Motel 2F-Entry Segment-Default'
        )
        assert i18n.tr_name('录像店-默认 + 汽车旅馆二层-房间段-默认') == (
            'Random Play-Default + Motel 2F-Room Segment-Default'
        )

    def test_unknown_parts_stay_as_typed(self, lang) -> None:
        lang('en')
        assert i18n.tr_name('我的路线') == '我的路线'
        assert i18n.tr_name('录像店-我的路线') == 'Random Play-我的路线'
        assert i18n.tr_name('My route-2') == 'My route-2'

    def test_zh_and_empty_unchanged(self, lang) -> None:
        lang('zh')
        assert i18n.tr_name('录像店-非高危') == '录像店-非高危'
        lang('en')
        assert i18n.tr_name('') == ''

    def test_every_data_name_translates_without_cjk(self, lang) -> None:
        """内置出生点、房间、房间入口与内置路线名在 en 下一个中文字都不剩。"""
        lang('en')
        left = {name: i18n.tr_name(name) for name in data_names() if _CJK_RE.search(i18n.tr_name(name))}
        assert left == {}

    def test_every_data_name_part_is_in_catalog(self) -> None:
        catalog = load_catalog()
        missing = sorted(part for part in data_name_part_keys() if part not in catalog)
        assert missing == []

    def test_names_in_catalog_text_match_tr_name(self, lang) -> None:
        """en.yml 里直接写出的名字与 tr_name 的结果一致。"""
        lang('en')
        catalog = load_catalog()
        notice = next(v for k, v in catalog.items() if k.startswith('1. 困难和常规会刷出'))
        for name in ('录像店-非高危', '白鸽工地站-非高危'):
            assert f'"{i18n.tr_name(name)}"' in notice.replace('\\"', '"')

    def test_status_name_fields_are_translated(self, lang) -> None:
        lang('en')
        assert i18n.tr_status('出生点 录像店') == 'spawn point Random Play'
        assert i18n.tr_status('多个出生点同时命中：录像店、白鸽工地站') == (
            'multiple spawn points matched: Random Play, Dove Street Construction Site Station'
        )
        assert i18n.tr_status('路线「录像店-默认」连续失败 3 局') == (
            'route "Random Play-Default" consecutive failures 3 rounds'
        )

    def test_route_messages_use_display_names(self, lang) -> None:
        from bagel_mac.route.route_model import (
            BagelRoute,
            BagelRoutePoint,
            BagelSpawnPoint,
            check_route_start,
        )

        spawn = BagelSpawnPoint('sp1', '录像店', '雅努斯幻境/Custom', 0.0, 0.0, 10.0)
        route = BagelRoute('r1', 'route', 'sp1')
        route.points.append(BagelRoutePoint(500.0, 0.0, '雅努斯幻境/Custom', 'beacon'))
        lang('en')
        assert check_route_start(route, spawn) == (
            'First point is 500px from Random Play, beyond the 10px detection radius'
        )
        custom = BagelSpawnPoint('sp2', '我的点', '雅努斯幻境/Custom', 0.0, 0.0, 10.0)
        assert '我的点' in check_route_start(route, custom)
        lang('zh')
        assert check_route_start(route, spawn) == '首点距录像店 500px，超出识别半径 10px'


_JS_FUNCTIONS = ('t', 'tn', 'tName', 'fmt')


def _page_js_helpers() -> str:
    """从 index.html 里摘出 t / tn / tName / fmt 的源码，供 node 直接执行。"""
    html = INDEX_HTML_PATH.read_text(encoding='utf-8')
    chunks: list[str] = []
    for name in _JS_FUNCTIONS:
        start = html.index(f'function {name}(')
        depth = 0
        for index in range(html.index('{', start), len(html)):
            if html[index] == '{':
                depth += 1
            elif html[index] == '}':
                depth -= 1
                if depth == 0:
                    chunks.append(html[start:index + 1])
                    break
    return '\n'.join(chunks)


def _run_js(lang_code: str, catalog: dict[str, str], expression: str) -> object:
    """在 node 里带上词典执行表达式，返回 JSON 结果；没有 node 时跳过。"""
    node = shutil.which('node')
    if node is None:
        pytest.skip('没有 node，跳过网页脚本测试')
    script = (
        f'const window = {{BAGEL_LANG: {json.dumps(lang_code)}, BAGEL_I18N: {json.dumps(catalog)}}};\n'
        f'{_page_js_helpers()}\n'
        f'console.log(JSON.stringify({expression}));'
    )
    out = subprocess.run([node, '-e', script], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


class TestPageScript:
    """网页里的 tn / tName 与 Python 的 tr_n / tr_name 行为一致。"""

    def test_tn(self) -> None:
        catalog = load_catalog()
        assert _run_js('en', catalog, "[tn(' 条路线 ', 1), tn(' 条路线 ', 2), tn(' 条路线 ', 0)]") == [
            ' route ', ' routes ', ' routes ',
        ]
        assert _run_js('zh', {}, "[tn(' 条路线 ', 1), tn(' 条路线 ', 2)]") == [' 条路线 ', ' 条路线 ']

    def test_tn_without_singular_entry_falls_back(self) -> None:
        assert _run_js('en', load_catalog(), "tn('雅努斯幻境', 1)") == 'Janus Illusion'

    def test_tname_matches_python(self, lang) -> None:
        catalog = load_catalog()
        names = sorted(data_names()) + ['我的路线', '录像店-我的路线', '录像店-默认 + 汽车旅馆二层-房间段-默认', '']
        expression = f'{json.dumps(names, ensure_ascii=False)}.map(n => tName(n))'
        lang('en')
        assert _run_js('en', catalog, expression) == [i18n.tr_name(name) for name in names]
        assert _run_js('zh', {}, expression) == names


_HTML_NAME_OBJECTS = r'\b(?:s|spawn|r|route|room|source|current|roomSeg|anchor)\.name\b'

# 页面里允许直接用原名的行（子串匹配），按原因分组。原名只用于比较、查找、保存，
# 渲染时必须包 tName()。新增使用 .name 的渲染代码时，要么包 tName，要么在这里登记并写明原因。
HTML_RAW_NAME_ALLOWED: dict[str, tuple[str, ...]] = {
    'comparison': (
        'state.routes.some(r => r.name === name',
        'state.routes.map(r => r.name)',
    ),
    'default_name_for_new_route': (
        "const base = anchor && anchor.isRoom ? anchor.name + '-房间段'",
        "const base = anchor && anchor.isRoom ? anchor.name + '-房间段录制'",
        "const base = source.name.replace(/-默认$/, '') + '-副本';",
    ),
    'editable_input_value': (
        'id="renameName" maxlength="40" value="',
    ),
    'state_copy': (
        'name: r.name, layer: r.layer, map_id: r.map_id,',
    ),
}


def untranslated_name_uses(html: str) -> list[str]:
    """页面里没包 tName() 也没登记为原名用途的 ``<对象>.name`` 使用处。"""
    allowed = [item for group in HTML_RAW_NAME_ALLOWED.values() for item in group]
    problems: list[str] = []
    for lineno, line in enumerate(html.split('\n'), 1):
        if line.strip().startswith('//'):
            continue
        for match in re.finditer(_HTML_NAME_OBJECTS, line):
            if line[: match.start()].endswith('tName('):
                continue
            if any(item in line for item in allowed):
                continue
            problems.append(f'{lineno}: {line.strip()[:110]}')
    return problems


class TestHtmlNames:
    """渲染出生点、房间、路线名的地方都包了 tName()。"""

    def test_every_rendered_name_goes_through_tname(self) -> None:
        assert untranslated_name_uses(INDEX_HTML_PATH.read_text(encoding='utf-8')) == []

    def test_guard_flags_unwrapped_use(self) -> None:
        html = "el.innerHTML = escapeHtml(s.name);\nel.title = escapeHtml(tName(r.name));\n"
        assert len(untranslated_name_uses(html)) == 1

    def test_raw_name_allowlist_entries_still_exist(self) -> None:
        html = INDEX_HTML_PATH.read_text(encoding='utf-8')
        stale = [item for group in HTML_RAW_NAME_ALLOWED.values() for item in group if item not in html]
        assert stale == []


class TestTemplateKeys:
    """带占位符的词典键：英文模板能被 tr_status 的整串匹配正常渲染。"""

    def test_every_template_key_renders(self, lang) -> None:
        """用每个键自己的占位符造一个样例文本，tr_status 渲染不抛错、不残留花括号。"""
        lang('en')
        failures: list[str] = []
        for key in load_catalog():
            if key.endswith(ONE_SUFFIX) or not _FORMAT_FIELD_RE.search(key):
                continue
            sample = re.sub(r'\{[^{}]*\}', '1', key)
            try:
                out = i18n.tr_status(sample)
            except Exception as error:  # noqa: BLE001 - 把所有渲染失败都收集起来
                failures.append(f'{key!r}: {error!r}')
                continue
            if re.search(r'\{[A-Za-z_][A-Za-z_0-9]*[^{}]*\}', out):
                failures.append(f'{key!r}: 残留占位符 {out!r}')
        assert failures == []

    def test_format_spec_templates(self, lang) -> None:
        lang('en')
        assert i18n.tr_status('慢步停车等转向 90度') == 'slow step stopped, waiting to turn 90 degrees'
        assert i18n.tr_status('停车等转向 12度') == 'stopped, waiting to turn 12 degrees'

    def test_nested_status_is_translated(self, lang) -> None:
        lang('en')
        assert i18n.tr_status('入箱失败：搜查面板状态持续不明') == 'loading failed: search panel state stays unclear'


class TestWalkerNodes:
    """悬浮窗显示的节点名：每个 operation_node 都要有词典条目。"""

    def test_every_walker_node_has_entry(self) -> None:
        catalog = load_catalog()
        names: set[str] = set()
        tree = ast.parse((PLUGIN_DIR / 'route' / 'route_walker.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for deco in node.decorator_list:
                    if (
                        isinstance(deco, ast.Call)
                        and isinstance(deco.func, ast.Name)
                        and deco.func.id == 'operation_node'
                    ):
                        for keyword in deco.keywords:
                            if keyword.arg == 'name' and isinstance(keyword.value, ast.Constant):
                                names.add(keyword.value.value)
        assert names
        assert sorted(name for name in names if name not in catalog) == []

    def test_overlay_lines_translate_node_and_name(self, lang) -> None:
        pytest.importorskip('PySide6')
        from bagel_mac.gui.route_test_thread import decision_lines

        snapshot = {
            'decision': '导航移动 · 已到点',
            'route_name': '录像店-默认',
            'cursor': 1,
            'total': 5,
            'points': [('beacon', (0, 0)), ('electronic_safe', (1, 1))],
            'distance': 12.0,
        }
        lang('en')
        lines = decision_lines(snapshot)
        assert lines[0] == 'node navigation move'
        assert lines[2] == 'route Random Play-Default · 1/5'
        assert not any(_CJK_RE.search(line) for line in lines)
        lang('zh')
        assert decision_lines(snapshot)[:3] == ['节点 导航移动', '状态 已到点', '路线 录像店-默认 · 1/5']


class TestKeyLegend:
    """路线编辑器顶部栏的打点键位说明：en 下不含中文，zh 与原文一致。"""

    @staticmethod
    def _texts() -> list[str]:
        pytest.importorskip('PySide6')
        pytest.importorskip('qfluentwidgets')
        from PySide6.QtWidgets import QApplication, QLabel, QWidget

        QApplication.instance() or QApplication([])
        from bagel_mac.gui.route_web_editor import BagelRouteEditor

        bar = QWidget()
        holder = BagelRouteEditor._build_key_hint(None, bar)  # noqa: SLF001 - 方法不依赖 self
        return [label.text() for label in holder.findChildren(QLabel) if label.text()]

    def test_en_legend_has_no_cjk(self, lang) -> None:
        lang('en')
        texts = self._texts()
        assert [text for text in texts if _CJK_RE.search(text)] == []
        assert 'Waypoint' in texts and 'Keycard Door' in texts or 'Keycard door' in texts

    def test_zh_legend_unchanged(self, lang) -> None:
        from bagel_mac.route.route_model import POINT_KINDS

        lang('zh')
        texts = self._texts()
        for _kind, label in POINT_KINDS.values():
            assert label in texts
        assert '退格' in texts and '删上一个点' in texts

    def test_point_kind_labels_all_have_entries(self) -> None:
        """POINT_KINDS / KIND_LABELS / BOX_CATEGORIES / SEGMENT_LABELS 的显示名都有词典条目。"""
        from bagel_mac.route.route_model import (
            BOX_CATEGORIES,
            KIND_LABELS,
            POINT_KINDS,
            SEGMENT_LABELS,
        )

        catalog = load_catalog()
        labels = {label for _, label in POINT_KINDS.values()}
        labels |= set(KIND_LABELS.values()) | set(BOX_CATEGORIES.values()) | set(SEGMENT_LABELS.values())
        assert sorted(label for label in labels if label not in catalog) == []


class TestRecorderMessages:
    """录制器的提示文字：en 走词典，zh 与改前逐字一致。"""

    def test_first_point_problem(self, lang) -> None:
        from bagel_mac.route.route_model import BagelRoutePoint, BagelSpawnPoint
        from bagel_mac.route.route_recorder import BagelPointRecorder

        recorder = BagelPointRecorder.__new__(BagelPointRecorder)
        recorder.spawn = BagelSpawnPoint('sp1', '录像店', '雅努斯幻境/Custom', 0.0, 0.0, 10.0)
        point = BagelRoutePoint(0.0, 0.0, '雅努斯幻境/Custom', 'door')
        lang('en')
        assert recorder._first_point_problem(point) == 'The first point must be a waypoint (press 1); it is Door'
        lang('zh')
        assert recorder._first_point_problem(point) == '首个点必须是途径点（按 1），当前是门'
        far = BagelRoutePoint(100.0, 0.0, '雅努斯幻境/Custom', 'beacon')
        assert recorder._first_point_problem(far) == '首个点距录像店 100px，需在 10px 内'
        lang('en')
        assert recorder._first_point_problem(far) == 'The first point is 100px from Random Play; it must be within 10px'
