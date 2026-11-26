"""打开快捷手册作战页：大世界按钮要点模板匹配位置，不点区域中心。

PlayCover 上「快捷手册」区域矩形按 Windows 布局标定，区域中心会落在图标之间的空隙；
必须点模板实际匹配到的位置（匹配中心 + 区域左上角）。
"""

from unittest.mock import Mock

import numpy as np
import pytest
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.base.matcher.match_result import MatchResult, MatchResultList
from one_dragon.base.screen.screen_area import ScreenArea

from bagel_mac.operation.open_compendium import BagelOpenCompendium


def _compendium_area() -> ScreenArea:
    """与 screen_info 里「大世界-普通 / 快捷手册」一致的模板区域。"""
    return ScreenArea(
        area_name='快捷手册',
        area_type='template',
        pc_rect=Rect(1440, 29, 1679, 140),
        template_id='compendium_playcover',
        template_sub_dir='normal_world',
        template_match_threshold=0.6,
        pc_alt=True,
    )


def test_open_combat_clicks_matched_position_not_area_center(monkeypatch: pytest.MonkeyPatch) -> None:
    """模板命中时应点匹配中心偏移后的位置，且不是区域矩形中心。"""
    op = BagelOpenCompendium(Mock())
    monkeypatch.setattr(op, '_at_combat_tab', lambda: False)
    monkeypatch.setattr(op, '_in_compendium', lambda: False)
    monkeypatch.setattr(op, '_has', lambda *args, **kwargs: True)
    op.last_screenshot = np.zeros((1080, 1920, 3), dtype=np.uint8)

    area = _compendium_area()
    op.ctx.screen_loader.get_area = Mock(return_value=area)

    mrl = MatchResultList(only_best=True)
    mrl.append(MatchResult(0.8, 47, 50, 51, 47))
    op.ctx.tm.crop_and_match_template = Mock(return_value=mrl)
    op.ctx.controller.click = Mock(return_value=True)

    result = op.open_combat()

    op.ctx.controller.click.assert_called_once()
    call = op.ctx.controller.click.call_args
    pos = call.args[0] if call.args else call.kwargs['pos']
    assert pos.tuple() == (mrl.max.center + area.rect.left_top).tuple()
    assert pos.tuple() != area.center.tuple()
    assert call.kwargs.get('pc_alt') is True
    assert op.open_clicks == 1
    assert result.status == '已点击快捷手册按钮'
