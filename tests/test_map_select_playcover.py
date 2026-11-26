"""选图页区域坐标：PlayCover 实机帧上必须读到前往备战、选中地图、推荐价值和页签。

PlayCover 窗口约 2:1，被拉伸成 1920x1080，选图页按钮和标题比 Windows 16:9 布局整体偏下，
Windows 标定的矩形会把文字切掉。这里用真实 OCR 在存档实机帧上跑一遍插件自己的读取逻辑。

没有 OCR 模型（主仓 assets/models/onnx_ocr）时跳过。
"""

from pathlib import Path
from types import SimpleNamespace

import cv2
import pytest
import yaml
from one_dragon.base.matcher.ocr.ocr_service import OcrService
from one_dragon.base.matcher.ocr.onnx_ocr_matcher import (
    DEFAULT_OCR_MODEL_NAME,
    OnnxOcrMatcher,
    get_final_file_list,
)
from one_dragon.base.screen.screen_info import ScreenInfo
from one_dragon.utils import str_utils
from one_dragon.utils.i18_utils import gt

from bagel_mac.defs import DIFFICULTY_HARD, MAP_JANUS
from bagel_mac.operation.enter import BagelEnter
from bagel_mac.vision.screen import read_area, read_coin_value

REPO_DIR = Path(__file__).resolve().parents[1]
MAP_YML = REPO_DIR / 'bagel_mac' / 'screen_info' / 'bagel_map.yml'
FRAME = REPO_DIR / 'docs' / 'materials' / 'screens' / 'playcover_map_select.webp'

#: 按 Windows 布局标定的旧矩形（改前）。只列在 PlayCover 帧上读不到的几项。
WINDOWS_RECTS: dict[str, list[int]] = {
    '前往备战': [1480, 920, 1740, 990],
    '选中地图': [630, 210, 900, 255],
    '推荐价值': [1282, 925, 1450, 982],
}


@pytest.fixture(scope='module')
def ocr_service() -> OcrService:
    if not all(Path(path).exists() for path in get_final_file_list(DEFAULT_OCR_MODEL_NAME)):
        pytest.skip('缺少 OCR 模型')
    matcher = OnnxOcrMatcher()
    if not matcher.init_model():
        pytest.skip('OCR 模型加载失败')
    return OcrService(matcher)


@pytest.fixture(scope='module')
def frame():
    """存档帧是 BGR 读入，转成插件运行时的 RGB。"""
    return cv2.cvtColor(cv2.imread(str(FRAME), cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)


def make_ctx(ocr_service: OcrService, overrides: dict[str, list[int]] | None = None) -> SimpleNamespace:
    """用 bagel_map.yml 建真实区域；overrides 把指定区域的矩形换成旧值。"""
    data = yaml.safe_load(MAP_YML.read_text(encoding='utf-8'))
    for area in data['area_list']:
        if overrides and area['area_name'] in overrides:
            area['pc_rect'] = overrides[area['area_name']]
    areas = {area.area_name: area for area in ScreenInfo(data).area_list}
    return SimpleNamespace(
        ocr_service=ocr_service,
        screen_loader=SimpleNamespace(get_area=lambda _screen, name: areas.get(name)),
    )


def found(ctx: SimpleNamespace, frame, name: str) -> bool:
    """与 round_by_find_area 同一判据：区域内任一 OCR 结果与目标文字的 LCS 达标。"""
    area = ctx.screen_loader.get_area('贝果-选图', name)
    matches = ctx.ocr_service.get_ocr_result_list(
        frame, rect=area.rect, color_range=area.color_range, crop_first=True,
    )
    return any(
        str_utils.find_by_lcs(gt(area.text, 'game'), match.data, percent=area.lcs_percent)
        for match in matches
    )


def make_op(ctx: SimpleNamespace, frame) -> BagelEnter:
    op = BagelEnter(ctx, difficulty=DIFFICULTY_HARD, map_name=MAP_JANUS)
    op.last_screenshot = frame
    return op


def test_frame_is_masked_and_small() -> None:
    assert FRAME.stat().st_size < 300 * 1024


def test_go_prepare_button_found(ocr_service: OcrService, frame) -> None:
    assert found(make_ctx(ocr_service), frame, '前往备战')


def test_windows_rects_miss_on_playcover(ocr_service: OcrService, frame) -> None:
    ctx = make_ctx(ocr_service, WINDOWS_RECTS)
    assert not found(ctx, frame, '前往备战')
    assert read_area(ctx, frame, '贝果-选图', '选中地图') != MAP_JANUS
    assert read_coin_value(ctx, frame, '贝果-选图', '推荐价值') == ''


def test_selected_map_reads_janus(ocr_service: OcrService, frame) -> None:
    assert read_area(make_ctx(ocr_service), frame, '贝果-选图', '选中地图') == MAP_JANUS


def test_recommended_value_reads_72100(ocr_service: OcrService, frame) -> None:
    assert read_coin_value(make_ctx(ocr_service), frame, '贝果-选图', '推荐价值') == '72100'


@pytest.mark.parametrize('name', ['常规', '困难', '高危', '雅努斯', '城郊幻境'])
def test_tabs_and_map_cards_found(ocr_service: OcrService, frame, name: str) -> None:
    assert found(make_ctx(ocr_service), frame, name)


def test_enter_nodes_pass_on_playcover_frame(ocr_service: OcrService, frame) -> None:
    """跑插件自己的节点：选图页识别、地图核对、难度核对都应通过。"""
    op = make_op(make_ctx(ocr_service), frame)
    assert op.open_map().is_success
    assert op._map_state() == 'ok'
    assert op.choose_map().status == '已核对{v0}'.format(v0=MAP_JANUS)
    assert op.choose_difficulty().is_success


def test_enter_nodes_fail_with_windows_rects(ocr_service: OcrService, frame) -> None:
    """改前的矩形让 open_map 误以为不在选图页，并要求回主页面（现场的循环 4 次）。"""
    op = make_op(make_ctx(ocr_service, WINDOWS_RECTS), frame)
    assert not op.open_map().is_success
    assert op._map_state() == 'unknown'
