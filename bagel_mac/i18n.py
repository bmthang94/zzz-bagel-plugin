"""插件自带的界面翻译。

主程序的 ``gt`` 只读主程序自己的文案目录，不认识插件的字符串，
所以插件另放一份 ``assets/i18n/<语言>.yml``（中文原文 → 译文）扁平词典。

查找顺序：语言是中文 → 返回原文；否则插件词典 → 主程序 ``gt`` → 原文。
词典缺失、语言不认识都回退原文，后续加新语言只需要补一份对应 yml。

只在显示时调用 ``tr``，配置值、节点状态、OCR 目标等内部字符串保持中文原文。
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


_PLACEHOLDER_RE = re.compile(r'\{[^{}]+\}')
_template_cache: dict[str, list[tuple[re.Pattern[str], str, list[str]]]] = {}
"""状态文本模板缓存：``{名字}`` 占位符编成整串匹配的正则，按语言各一份。"""


def _templates(lang: str) -> list[tuple[re.Pattern[str], str, list[str]]]:
    """把词典里带 ``{占位符}`` 的键编成 ``(正则, 英文模板, 占位符名)`` 列表。"""
    if lang in _template_cache:
        return _template_cache[lang]
    compiled: list[tuple[re.Pattern[str], str, list[str]]] = []
    for zh_key, en_value in _load_catalog(lang).items():
        names = _PLACEHOLDER_RE.findall(zh_key)
        if not names:
            continue
        field_names = [name[1:-1] for name in names]
        parts = _PLACEHOLDER_RE.split(zh_key)
        pattern = ''
        for index, part in enumerate(parts):
            pattern += re.escape(part)
            if index < len(parts) - 1:
                pattern += f"(?P<g{index}>.+?)"
        try:
            compiled.append((re.compile(f'\\A{pattern}\\Z'), en_value, field_names))
        except re.error:
            continue
    _template_cache[lang] = compiled
    return compiled


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
    for pattern, en_template, field_names in _templates(lang):
        match = pattern.match(text)
        if match is not None:
            return en_template.format(**dict(zip(field_names, match.groups(), strict=False)))
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
