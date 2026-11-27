"""PlayCover 水平镜头手势与转向分流。"""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bagel_mac.route import input_worker, playcover_turn
from bagel_mac.route.input_worker import BagelInputWorker


@pytest.fixture
def gesture(monkeypatch: pytest.MonkeyPatch) -> tuple[SimpleNamespace, list[tuple]]:
    events: list[tuple] = []
    mouse = SimpleNamespace(
        nudge_cursor=lambda: events.append(('nudge',)),
        move_relative=lambda x, y: events.append(('move', x, y)),
        press=lambda button: events.append(('press', button)),
        release=lambda button: events.append(('release', button)),
    )
    win = SimpleNamespace(is_win_active=True, game2win_pos=lambda point: point)
    controller = SimpleNamespace(
        _touch_lock=threading.RLock(),
        _touch_held=False,
        _touch_owner=None,
        game_win=win,
        _get_mac_virtual_hid_mouse=lambda: mouse,
    )
    monkeypatch.setitem(
        sys.modules, 'pyautogui',
        SimpleNamespace(moveTo=lambda x, y, **kwargs: events.append(('moveTo', x, y, kwargs))),
    )
    monkeypatch.setattr(playcover_turn, 'time', SimpleNamespace(sleep=lambda _: None, monotonic=lambda: 0))
    return controller, events


def test_small_flicks_are_mirrored_and_horizontal(gesture: tuple[SimpleNamespace, list[tuple]]) -> None:
    controller, events = gesture
    assert playcover_turn.flick_camera(controller, 40) is True
    positive = list(events)
    events.clear()
    assert playcover_turn.flick_camera(controller, -40) is True
    negative = list(events)

    for sequence in (positive, negative):
        assert [event for event in sequence if event[0] == 'press'] == [('press', 'left')]
        assert [event for event in sequence if event[0] == 'release'] == [('release', 'left')]
        assert all(event[2] == 0 for event in sequence if event[0] == 'move')
        assert [event[0] for event in sequence].index('moveTo') < [event[0] for event in sequence].index('press')
        assert sequence[0][3] == {'_pause': False}
    assert [(e[1], e[2]) for e in positive if e[0] == 'move'] == [
        (-e[1], e[2]) for e in negative if e[0] == 'move'
    ]
    assert positive[0][1] + negative[0][1] == 1920
    start = positive.index(('press', 'left'))
    assert sum(e[1] for e in positive[start:] if e[0] == 'move') == 30


def test_move_exception_after_press_releases(gesture: tuple[SimpleNamespace, list[tuple]]) -> None:
    controller, events = gesture
    mouse = controller._get_mac_virtual_hid_mouse()

    def move(x: int, y: int) -> None:
        if ('press', 'left') in events:
            raise RuntimeError('move failed')
        events.append(('move', x, y))

    mouse.move_relative = move
    with pytest.raises(RuntimeError, match='move failed'):
        playcover_turn.flick_camera(controller, 40)
    assert events[-1] == ('release', 'left')
    assert events.count(('press', 'left')) == events.count(('release', 'left')) == 1


def test_release_failure_propagates(gesture: tuple[SimpleNamespace, list[tuple]]) -> None:
    controller, _events = gesture
    mouse = controller._get_mac_virtual_hid_mouse()
    mouse.release = Mock(side_effect=RuntimeError('release failed'))
    with pytest.raises(RuntimeError, match='release failed'):
        playcover_turn.flick_camera(controller, 40)
    mouse.release.assert_called_once_with('left')


@pytest.mark.parametrize('held, owner', [(True, 'move'), (False, 'move')])
def test_occupied_touch_sends_nothing(
    gesture: tuple[SimpleNamespace, list[tuple]], held: bool, owner: str,
) -> None:
    controller, events = gesture
    controller._touch_held = held
    controller._touch_owner = owner
    assert playcover_turn.flick_camera(controller, 40) is False
    assert events == []


@pytest.mark.parametrize('distance', [0.0, float('inf'), float('-inf'), float('nan')])
def test_invalid_distance_sends_nothing(
    gesture: tuple[SimpleNamespace, list[tuple]], distance: float,
) -> None:
    controller, events = gesture
    assert playcover_turn.flick_camera(controller, distance) is False
    assert events == []


def test_inactive_window_sends_nothing(gesture: tuple[SimpleNamespace, list[tuple]]) -> None:
    controller, events = gesture
    controller.game_win.is_win_active = False
    assert playcover_turn.flick_camera(controller, 40) is False
    assert events == []


def test_focus_loss_during_drag_releases(gesture: tuple[SimpleNamespace, list[tuple]]) -> None:
    controller, events = gesture
    mouse = controller._get_mac_virtual_hid_mouse()
    original_press = mouse.press

    def press(button: str) -> None:
        original_press(button)
        controller.game_win.is_win_active = False

    mouse.press = press
    assert playcover_turn.flick_camera(controller, 40) is False
    assert events[-2:] == [('press', 'left'), ('release', 'left')]


@pytest.mark.parametrize(
    'platform, touch, enabled, expect_flick',
    [('darwin', True, True, True), ('darwin', False, True, False), ('win32', True, True, False)],
)
def test_run_turn_routes_by_platform(
    monkeypatch: pytest.MonkeyPatch, platform: str, touch: bool, enabled: bool, expect_flick: bool,
) -> None:
    controller = SimpleNamespace(
        game_config=SimpleNamespace(turn_dx=-5.5),
        touch_ui_enabled=touch,
        turn_by_angle_diff=Mock(),
    )
    worker = object.__new__(BagelInputWorker)
    worker.ctx = SimpleNamespace(controller=controller)
    worker._mouse_lock = threading.Lock()
    worker._turn_enabled = enabled
    flick = Mock(return_value=True)
    monkeypatch.setattr(input_worker, 'sys', SimpleNamespace(platform=platform))
    monkeypatch.setattr(input_worker, 'flick_camera', flick)
    assert worker._run_turn(10) is True
    if expect_flick:
        flick.assert_called_once_with(controller, -55.0)
        controller.turn_by_angle_diff.assert_not_called()
    else:
        flick.assert_not_called()
        controller.turn_by_angle_diff.assert_called_once_with(10)


def test_run_turn_disabled_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = SimpleNamespace(
        game_config=SimpleNamespace(turn_dx=-5.5),
        touch_ui_enabled=True,
        turn_by_angle_diff=Mock(),
    )
    worker = object.__new__(BagelInputWorker)
    worker.ctx = SimpleNamespace(controller=controller)
    worker._mouse_lock = threading.Lock()
    worker._turn_enabled = False
    flick = Mock()
    monkeypatch.setattr(input_worker, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(input_worker, 'flick_camera', flick)
    assert worker._run_turn(10) is False
    flick.assert_not_called()
    controller.turn_by_angle_diff.assert_not_called()


def test_run_turn_propagates_skipped_gesture(monkeypatch: pytest.MonkeyPatch) -> None:
    controller = SimpleNamespace(
        game_config=SimpleNamespace(turn_dx=-5.5),
        touch_ui_enabled=True,
        turn_by_angle_diff=Mock(),
    )
    worker = object.__new__(BagelInputWorker)
    worker.ctx = SimpleNamespace(controller=controller)
    worker._mouse_lock = threading.Lock()
    worker._turn_enabled = True
    flick = Mock(return_value=False)
    monkeypatch.setattr(input_worker, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(input_worker, 'flick_camera', flick)
    assert worker._run_turn(10) is False
    flick.assert_called_once_with(controller, -55.0)
    controller.turn_by_angle_diff.assert_not_called()
