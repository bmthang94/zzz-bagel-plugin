"""插件自带的界面翻译。

主程序的 ``gt`` 只读主程序自己的文案目录，不认识插件的字符串，
所以插件另放一份 ``assets/i18n/<语言>.yml``（中文原文 → 译文）扁平词典。

查找顺序：语言是中文 → 返回原文；否则插件词典 → 主程序 ``gt`` → 原文。
词典缺失、语言不认识都回退原文，后续加新语言只需要补一份对应 yml。

只在显示时调用 ``tr``，配置值、节点状态、OCR 目标等内部字符串保持中文原文。

另有两个显示专用的入口：``tr_n`` 按数量选单复数，``tr_name`` 翻译出生点、房间、
内置路线这类名字（数据仍是中文，只在渲染处翻）。
"""

from __future__ import annotations

import json
import re

import yaml
from one_dragon.utils import i18_utils
from one_dragon.utils.i18_utils import gt

from . import defs

_catalog_cache: dict[str, dict[str, str]] = {}
"""已加载的词典缓存，键为语言代码。"""


def current_lang() -> str:
    """当前界面语言代码，跟随主程序的语言设置。"""
    return i18_utils.get_default_lang()


def _is_zh(lang: str) -> bool:
    """判断语言代码是否中文（``zh`` 及 ``zh-CN`` 这类前缀）。"""
    return lang == 'zh' or lang.startswith('zh')


def _load_catalog(lang: str) -> dict[str, str]:
    """按语言代码加载插件词典；文件缺失或内容不是扁平字典时返回空表。"""
    if lang in _catalog_cache:
        return _catalog_cache[lang]
    catalog: dict[str, str] = {}
    path = defs.PLUGIN_DIR / 'assets' / 'i18n' / f'{lang}.yml'
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            catalog = {str(k): str(v) for k, v in data.items() if v is not None}
    _catalog_cache[lang] = catalog
    return catalog


def tr(msg: str) -> str:
    """把插件的中文原文翻成当前语言；找不到译文时保持原文。

    Args:
        msg: 代码里的中文字符串原文。

    Returns:
        当前语言下应显示的文本。
    """
    if not msg:
        return msg
    lang = current_lang()
    if _is_zh(lang):
        return msg
    translated = _load_catalog(lang).get(msg)
    if translated:
        return translated
    return gt(msg)


ONE_SUFFIX = '|one'
"""单数形式词典键的后缀：``<中文原文>|one``。只有英文需要单数时才收录。"""


def tr_n(msg: str, n: int) -> str:
    """按数量翻译：非中文且 ``n == 1`` 时先查 ``msg|one`` 单数条目，没有就回退 :func:`tr`。

    中文不分单复数，恒返回原文。调用方仍自己 ``.format`` 填数量。

    Args:
        msg: 代码里的中文原文（复数形式的键）。
        n: 数量。

    Returns:
        当前语言下应显示的文本。
    """
    if n == 1 and msg and not _is_zh(current_lang()):
        singular = _load_catalog(current_lang()).get(msg + ONE_SUFFIX)
        if singular:
            return singular
    return tr(msg)


_NAME_SEPARATOR_RE = re.compile(r'(-|_| \+ )')
"""名字里的分隔符：出生点、路线名用 ``-`` 与 ``_`` 拼接各部分；开房卡试跑的合并路线名用 `` + `` 连接入口段与房间段。"""


def tr_name(name: str) -> str:
    """翻译出生点、房间、内置路线这类数据名字，只用于渲染。

    先整串精确匹配词典；没有就按 ``-`` / ``_`` / `` + `` 拆开，各部分单独查词典后按原分隔符
    拼回。查不到的部分保持原样，所以用户自己起的名字不受影响。不走主程序 ``gt``。
    名字本身仍是中文数据，比较、查找、保存一律用原名，不要拿这里的结果做这些事。

    Args:
        name: 中文原名。

    Returns:
        当前语言下应显示的名字；中文原样返回。
    """
    if not name or _is_zh(current_lang()):
        return name
    catalog = _load_catalog(current_lang())
    exact = catalog.get(name)
    if exact:
        return exact
    parts = _NAME_SEPARATOR_RE.split(name)
    return ''.join(part if index % 2 else catalog.get(part) or part for index, part in enumerate(parts))


_PLACEHOLDER_RE = re.compile(r'\{[^{}]+\}')
_CJK_RE = re.compile(r'[぀-ヿ一-鿿가-힯]')
_template_cache: dict[str, list[tuple[re.Pattern[str], str, list[str], str]]] = {}
"""状态文本模板缓存：``{名字}`` 占位符编成整串匹配的正则，按语言各一份。"""


_FORMAT_SPEC_RE = re.compile(r'\{([^{}:!]+)[^{}]*\}')
"""``{v0:.0f}`` 这类带格式说明的占位符：中文原文里数字已经格式化好了，英文模板去掉说明直接填。"""


def _field_name(placeholder: str) -> str:
    """``{v0:.0f}`` → ``v0``：占位符里取字段名，丢掉 ``!r`` / ``:.0f`` 之类的说明。"""
    return re.split(r'[!:]', placeholder[1:-1], maxsplit=1)[0]


def _templates(lang: str) -> list[tuple[re.Pattern[str], str, list[str], str]]:
    """把词典里带 ``{占位符}`` 的键编成 ``(正则, 英文模板, 占位符名, 字面文字)`` 列表，字面文字多的排前面。"""
    if lang in _template_cache:
        return _template_cache[lang]
    compiled: list[tuple[re.Pattern[str], str, list[str], str]] = []
    for zh_key, en_value in _load_catalog(lang).items():
        names = _PLACEHOLDER_RE.findall(zh_key)
        if not names:
            continue
        field_names = [_field_name(name) for name in names]
        parts = _PLACEHOLDER_RE.split(zh_key)
        pattern = ''
        for index, part in enumerate(parts):
            pattern += re.escape(part)
            if index < len(parts) - 1:
                pattern += f"(?P<g{index}>.+?)"
        try:
            compiled.append((
                re.compile(f'\\A{pattern}\\Z'), _FORMAT_SPEC_RE.sub(r'{\1}', en_value), field_names,
                _PLACEHOLDER_RE.sub('', zh_key),
            ))
        except re.error:
            continue
    # 字面文字多的模板更具体，先试；像 ``{count} 局`` 这种几乎全是占位符的通用模板排在最后。
    compiled.sort(key=lambda item: -len(item[3]))
    _template_cache[lang] = compiled
    return compiled


_NAME_FIELDS = frozenset({'name', 'spawn_name', 'room_name', 'route_name'})
"""模板里这些占位符装的是出生点、房间、路线名，填入前走 :func:`tr_name`。"""

_NAMES_FIELD = 'names'
"""占位符装的是用 ``、`` 连起来的一串名字：逐个 :func:`tr_name`，用 ``, `` 连接。"""


def _tr_status_value(field: str, value: str) -> str:
    """翻译模板抓到的值：名字走 ``tr_name``，内嵌的中文状态递归走 ``tr_status``，其余原样。"""
    if field in _NAME_FIELDS:
        return tr_name(value)
    if field == _NAMES_FIELD:
        return ', '.join(tr_name(item) for item in value.split('、'))
    if _CJK_RE.search(value):
        return tr_status(value)
    return value


def tr_status(text: str) -> str:
    """翻译带动态值的状态文本：先按整串精确匹配，再按 ``{占位符}`` 模板匹配。

    模板键里的每个 ``{名字}`` 编成一个非贪婪捕获组全串匹配，抓到的值原样填进
    英文模板；都不匹配时走 ``gt`` / 原文。状态原文保持中文，只在这里做展示翻译。
    """
    if not text:
        return text
    lang = current_lang()
    if _is_zh(lang):
        return text
    catalog = _load_catalog(lang)
    translated = catalog.get(text)
    if translated:
        return translated
    for pattern, en_template, field_names, _literal in _templates(lang):
        match = pattern.match(text)
        if match is not None:
            values = dict(zip(field_names, match.groups(), strict=False))
            return en_template.format(**{k: _tr_status_value(k, v) for k, v in values.items()})
    return gt(text)


def web_catalog() -> dict[str, str]:
    """当前语言的整份插件词典，给网页界面用；中文时为空表。"""
    lang = current_lang()
    if _is_zh(lang):
        return {}
    return dict(_load_catalog(lang))


def web_bootstrap_script() -> str:
    """生成注入网页的 ``<script>`` 片段：语言代码加整份词典。

    词典 JSON 里的 ``</`` 转成 ``<\\/``，``\\u2028`` / ``\\u2029`` 也转义，
    免得词典值意外截断 ``<script>`` 块。中文界面注入 ``BAGEL_LANG='zh'``
    与空词典，网页里的 ``t()`` 自然回退原文。
    """
    lang = current_lang()
    catalog = {} if _is_zh(lang) else web_catalog()
    payload = json.dumps(catalog, ensure_ascii=False)
    payload = payload.replace('</', '<\\/')
    payload = payload.replace(chr(0x2028), '\\u2028').replace(chr(0x2029), '\\u2029')
    return (
        '<script>'
        f'window.BAGEL_LANG={json.dumps(lang)};'
        f'window.BAGEL_I18N={payload};'
        '</script>'
    )


def clear_cache() -> None:
    """清空词典缓存，测试切换语言时用。"""
    _catalog_cache.clear()
    _template_cache.clear()
