"""README 里的相对链接与图片必须指向存在的文件，``.md#锚点`` 的锚点标题也要存在。

覆盖 README.md 与 README.en.md；http(s) 外链不检查。两份 README 的语言切换行互相指向对方。
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

import pytest

REPO_DIR = Path(__file__).resolve().parents[1]
README_FILES = ('README.md', 'README.en.md')

_LINK_RE = re.compile(r'!?\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
_FENCE_RE = re.compile(r'^```.*?^```', re.S | re.M)


def relative_links(text: str) -> list[str]:
    """Markdown 里的相对链接目标（含图片），忽略代码块、外链与页内锚点。"""
    text = _FENCE_RE.sub('', text)
    targets = []
    for target in _LINK_RE.findall(text):
        if target.startswith(('http://', 'https://', 'mailto:', '#')):
            continue
        targets.append(target)
    return targets


def heading_anchors(path: Path) -> set[str]:
    """按 GitHub 规则生成 Markdown 文件的标题锚点：小写、去标点、空格变连字符。"""
    anchors: set[str] = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        match = re.match(r'#{1,6}\s+(.*?)\s*#*$', line)
        if match:
            slug = re.sub(r'[^\w\- ]', '', match.group(1).strip().lower()).replace(' ', '-')
            anchors.add(slug)
    return anchors


def broken_links(readme: Path) -> list[str]:
    """README 里指向不存在文件或锚点的相对链接。"""
    problems: list[str] = []
    for target in relative_links(readme.read_text(encoding='utf-8')):
        raw_path, _, anchor = target.partition('#')
        path = (readme.parent / unquote(raw_path)).resolve()
        if not path.exists():
            problems.append(f'{readme.name}: {target} 文件不存在')
        elif anchor and path.suffix == '.md' and unquote(anchor) not in heading_anchors(path):
            problems.append(f'{readme.name}: {target} 锚点不存在')
    return problems


@pytest.mark.parametrize('name', README_FILES)
def test_readme_links_exist(name: str) -> None:
    assert broken_links(REPO_DIR / name) == []


@pytest.mark.parametrize('name', README_FILES)
def test_readme_has_images(name: str) -> None:
    """图片链接确实被扫描到（防止正则失效后测试恒过）。"""
    links = relative_links((REPO_DIR / name).read_text(encoding='utf-8'))
    assert sum(link.endswith('.png') for link in links) == 4


def test_language_switch_lines() -> None:
    zh = (REPO_DIR / 'README.md').read_text(encoding='utf-8').splitlines()
    en = (REPO_DIR / 'README.en.md').read_text(encoding='utf-8').splitlines()
    assert zh[2] == '简体中文 | [English](README.en.md)'
    assert en[2] == '[简体中文](README.md) | English'


def test_guard_flags_missing_targets(tmp_path: Path) -> None:
    (tmp_path / 'a.md').write_text('# 标题\n', encoding='utf-8')
    readme = tmp_path / 'README.md'
    readme.write_text(
        '![x](missing.png)\n[ok](a.md#标题)\n[bad](a.md#没有)\n[web](https://example.com/x.png)\n',
        encoding='utf-8',
    )
    problems = broken_links(readme)
    assert len(problems) == 2
    assert any('missing.png' in problem for problem in problems)
    assert any('锚点' in problem for problem in problems)


def test_old_image_paths_are_flagged(tmp_path: Path) -> None:
    readme = tmp_path / 'README.md'
    readme.write_text('![x](bagel/assets/docs/01-add-app.png)\n', encoding='utf-8')
    assert broken_links(readme) != []
