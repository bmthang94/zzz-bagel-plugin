"""用留档的 PlayCover 出生帧回归小地图裁剪缩放、定位与朝向。"""

from pathlib import Path

import cv2
import pytest
from cv2.typing import MatLike
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.base.screen.screen_loader import ScreenContext

from bagel_mac.vision.map_locator import (
    ACCEPT_SCORE,
    MultiMapLocator,
    crop_minimap,
    load_map_locators,
)
from bagel_mac.vision.minimap_angle import analyze

REPO_DIR = Path(__file__).resolve().parents[1]

SPAWN_JANUS_SOUTH = (1002, 466)
"""六昏街南站出生点全局坐标，登记半径 100。"""


def load_material_frame(name: str) -> MatLike:
    image = cv2.imread(str(REPO_DIR / 'docs/materials/screens' / name))
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


@pytest.fixture(scope='module')
def minimap_rect() -> Rect:
    """直接读画面建档里的「定位小地图」区域，防止测试与配置漂移。"""
    loader = ScreenContext()
    loader.load_extra_screen_dir(str(REPO_DIR / 'bagel_mac/screen_info'), 'bagel_mac')
    area = loader.get_area('贝果-局内', '定位小地图')
    assert area is not None
    return area.pc_rect


@pytest.fixture(scope='module')
def locators() -> list:
    return load_map_locators()


@pytest.mark.parametrize('fixture_name', [
    'playcover_spawn_janus_hr.webp',
    'playcover_hud_spawn.webp',
])
def test_spawn_locates_near_registered_point(locators: list, minimap_rect: Rect, fixture_name: str) -> None:
    frame = load_material_frame(fixture_name)
    crop = crop_minimap(frame, minimap_rect)
    assert crop is not None and crop.shape == (201, 201, 3)

    result = MultiMapLocator(locators).locate(crop)

    assert result is not None, '定位失败'
    locator, score, center = result
    assert locator.map_id == '雅努斯幻境'
    assert score >= ACCEPT_SCORE
    distance = ((center[0] - SPAWN_JANUS_SOUTH[0]) ** 2 + (center[1] - SPAWN_JANUS_SOUTH[1]) ** 2) ** 0.5
    assert distance <= 3, f'定位 {center} 离六昏街南站 {distance:.0f}px'


@pytest.mark.parametrize('fixture_name', [
    'playcover_spawn_janus_hr.webp',
    'playcover_hud_spawn.webp',
])
def test_spawn_heading_points_north(minimap_rect: Rect, fixture_name: str) -> None:
    frame = load_material_frame(fixture_name)
    crop = crop_minimap(frame, minimap_rect)
    assert crop is not None

    _marker, _edges, sector, _radius, _center = analyze(crop)

    assert sector is not None and 85 <= sector <= 105


def test_deep_site_timeout_heading(minimap_rect: Rect) -> None:
    path = REPO_DIR / 'docs/materials/minimap_heading/playcover/deep_site_timeout.webp'
    frame = cv2.cvtColor(cv2.imread(str(path)), cv2.COLOR_BGR2RGB)
    rect = Rect(minimap_rect.x1 - 1400, minimap_rect.y1 - 120,
                minimap_rect.x2 - 1400, minimap_rect.y2 - 120)
    crop = crop_minimap(frame, rect)
    assert crop is not None
    sector = analyze(crop)[2]
    assert sector is not None and 65 <= sector <= 100
