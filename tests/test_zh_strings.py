"""中文字符串守卫：插件源码里的中文字符串字面量要么翻译、要么登记为内部字符串。

扫描 ``bagel_mac/**/*.py`` 的每个含中日韩字符的字符串常量（f-string 的各段也算），
下面几类不算违规：

- 文档字符串，以及单独成行的字符串表达式（属性文档）；
- 直接作为 ``tr()`` / ``tr_n()`` / ``tr_name()`` / ``tr_status()`` / ``gt()`` 的首参；
- 装饰器参数（``@operation_node(name=...)``、``@node_from(...)`` 里的节点名与状态）；
- ``round_*`` 调用的参数（画面区域名、状态文本）和 ``STATUS_*`` / ``OUTCOME_*`` 常量
  ——它们的词典覆盖由 ``test_i18n.py`` 的状态守卫检查；
- 本身就是 ``en.yml`` 词典键的字符串：经 ``tr(变量)`` / ``tr_status()`` 显示，覆盖由词典测试保证；
- 登记在 ``zh_internal_strings.json`` 里的内部字符串，按原因分组。

其余都是漏翻的界面文字，测试失败。新增面向用户的文字：包 ``tr()`` 并在 ``en.yml`` 加条目；
确实是内部字符串（配置值、画面区域名、OCR 目标……）：登记到 ``zh_internal_strings.json``。
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable
from pathlib import Path

import yaml

REPO_DIR = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_DIR / 'bagel_mac'
ALLOWLIST_PATH = Path(__file__).with_name('zh_internal_strings.json')
EN_CATALOG_PATH = PLUGIN_DIR / 'assets' / 'i18n' / 'en.yml'

CJK_RE = re.compile(r'[぀-ヿ一-鿿가-힯]')

WRAPPERS = frozenset({'tr', 'tr_n', 'tr_name', 'tr_status', 'gt'})
"""首参允许直接写中文的翻译入口。"""

STATUS_PREFIXES = ('STATUS_', 'OUTCOME_', '_STATUS_')


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _assigned_name(node: ast.stmt) -> str:
    if isinstance(node, ast.Assign) and node.targets:
        target = node.targets[0]
    elif isinstance(node, ast.AnnAssign):
        target = node.target
    else:
        return ''
    if isinstance(target, ast.Attribute):
        return target.attr
    return target.id if isinstance(target, ast.Name) else ''


def structurally_exempt(node: ast.Constant, parents: dict[int, ast.AST]) -> bool:
    """字符串所处的位置本身就说明它不是界面文字：翻译入口首参、装饰器、round_* 参数、状态常量。"""
    child: ast.AST = node
    parent = parents.get(id(node))
    while parent is not None:
        if isinstance(parent, ast.Call):
            name = _call_name(parent.func) or ''
            if name in WRAPPERS and parent.args and child is parent.args[0]:
                return True
            if name.startswith('round_'):
                return True
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child in parent.decorator_list:
            return True
        if isinstance(parent, ast.stmt):
            return _assigned_name(parent).startswith(STATUS_PREFIXES)
        child = parent
        parent = parents.get(id(parent))
    return False


def find_cjk_strings(path: Path) -> list[tuple[int, str]]:
    """文件里不在豁免位置的含中文字符串常量，``(行号, 字符串)``。"""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str) and CJK_RE.search(node.value)):
            continue
        parent = parents.get(id(node))
        if isinstance(parent, ast.Expr):
            continue
        if structurally_exempt(node, parents):
            continue
        found.append((node.lineno, node.value))
    return found


def load_allowlist() -> dict[str, dict[str, list[str]]]:
    """``{原因: {相对路径: [字符串, ...]}}``。"""
    data = json.loads(ALLOWLIST_PATH.read_text(encoding='utf-8'))
    return {reason: files for reason, files in data.items() if not reason.startswith('_')}


def allowed_strings(allowlist: dict[str, dict[str, list[str]]]) -> dict[str, set[str]]:
    """``{相对路径: {字符串}}``，不分原因。"""
    merged: dict[str, set[str]] = {}
    for files in allowlist.values():
        for rel, strings in files.items():
            merged.setdefault(rel, set()).update(strings)
    return merged


def untranslated_strings(
    paths: Iterable[Path], catalog: set[str], allowed: dict[str, set[str]],
) -> list[str]:
    """漏翻的中文字符串：不是词典键、没登记、也不在豁免位置。"""
    problems: list[str] = []
    for path in paths:
        rel = path.relative_to(REPO_DIR).as_posix()
        for lineno, text in find_cjk_strings(path):
            if text in catalog or text in allowed.get(rel, set()):
                continue
            problems.append(f'{rel}:{lineno} {text[:50]!r}')
    return problems


def _catalog_keys() -> set[str]:
    return {str(key) for key in yaml.safe_load(EN_CATALOG_PATH.read_text(encoding='utf-8'))}


def _plugin_files() -> list[Path]:
    return sorted(PLUGIN_DIR.rglob('*.py'))


class TestChineseStringGuard:
    """源码里没有漏翻的中文界面字符串。"""

    def test_no_untranslated_chinese_strings(self) -> None:
        problems = untranslated_strings(
            _plugin_files(), _catalog_keys(), allowed_strings(load_allowlist()),
        )
        assert problems == []

    def test_allowlist_has_no_stale_entries(self) -> None:
        """登记过的字符串必须还在源码里，并且确实需要登记（不是词典键、不在豁免位置）。"""
        catalog = _catalog_keys()
        present: dict[str, set[str]] = {}
        for path in _plugin_files():
            present[path.relative_to(REPO_DIR).as_posix()] = {text for _, text in find_cjk_strings(path)}
        stale: list[str] = []
        for reason, files in load_allowlist().items():
            for rel, strings in files.items():
                for text in strings:
                    if text not in present.get(rel, set()):
                        stale.append(f'{reason} {rel} {text[:40]!r} 源码里已不存在')
                    elif text in catalog:
                        stale.append(f'{reason} {rel} {text[:40]!r} 已是词典键，不必登记')
        assert stale == []

    def test_allowlist_entries_are_unique_per_file(self) -> None:
        """同一个文件里的字符串只登记在一个原因下。"""
        seen: dict[tuple[str, str], str] = {}
        duplicates: list[str] = []
        for reason, files in load_allowlist().items():
            for rel, strings in files.items():
                for text in strings:
                    if (rel, text) in seen:
                        duplicates.append(f'{rel} {text!r}: {seen[(rel, text)]} / {reason}')
                    seen[(rel, text)] = reason
        assert duplicates == []

    def test_every_allowlist_reason_is_known(self) -> None:
        assert set(load_allowlist()) <= set(KNOWN_REASONS)

    def test_guard_flags_bare_chinese(self, tmp_path: Path) -> None:
        """扫描器本身要能查出问题：裸中文、f-string 段、没包 tr 的返回值都要报。"""
        source = tmp_path / 'sample.py'
        source.write_text(
            "def f(x):\n"
            "    '''文档字符串不算。'''\n"
            "    a = '裸中文界面文字'\n"
            "    b = f'带值 {x} 的文字'\n"
            "    c = tr('已翻译')\n"
            "    d = tr_name('名字')\n"
            "    self.round_fail('状态参数不算')\n"
            "    STATUS_X: str = '状态常量不算'\n"
            "    return '返回的中文'\n",
            encoding='utf-8',
        )
        texts = [text for _, text in find_cjk_strings(source)]
        assert '裸中文界面文字' in texts
        assert any('带值' in text for text in texts)
        assert '返回的中文' in texts
        assert '已翻译' not in texts and '名字' not in texts
        assert '状态参数不算' not in texts and '状态常量不算' not in texts
        assert '文档字符串不算。' not in texts


KNOWN_REASONS = (
    'data_key',
    'ocr_target',
    'config_value',
    'status_match',
    'game_text_table',
    'regex_pattern',
)
"""登记原因：

- ``data_key``：画面区域名、屏幕名、字典键、模板文件名这类与资源配置逐字对应的名字；
- ``ocr_target``：拿去和 OCR 识别结果 / 游戏文字比对的文字，翻译会让比对失效；
- ``config_value``：写进配置文件或插件清单的值，翻译会让已保存的配置失效；
- ``status_match``：和状态文本做比较的片段，状态本身在词典里；
- ``game_text_table``：游戏里的文字对照表（中文数字等）；
- ``regex_pattern``：正则表达式里的中日韩字符范围或匹配文字。
"""
