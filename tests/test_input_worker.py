"""前进线程的短按节拍：用真实线程和记录按键时刻的假控制器检查停顿下限。"""

import queue
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from bagel_mac.route import input_worker
from bagel_mac.route.input_worker import STEP_REST_SECONDS, BagelInputWorker


class _Keys:
    """只记录 W 的按下和松开时刻。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, float]] = []

    def move_w(self, press: bool = False, release: bool = False) -> None:
        self.events.append(('down' if press else 'up', time.time()))

    def rests(self) -> list[float]:
        """每次松开到下一次按下的间隔。"""
        return [b[1] - a[1] for a, b in zip(self.events, self.events[1:], strict=False)
                if a[0] == 'up' and b[0] == 'down']


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> tuple[BagelInputWorker, _Keys]:
    keys = _Keys()
    ctx = Mock()
    ctx.controller.move_w = keys.move_w
    monkeypatch.setattr(input_worker._TurnWorker, '_loop', lambda self: None)
    result = BagelInputWorker(ctx)
    yield result, keys
    result.shutdown()


def _wait(predicate, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, '等待超时'
        time.sleep(0.002)


def _record_turn(sent: list[float]) -> Callable[[float], bool]:
    def record(angle: float) -> bool:
        sent.append(angle)
        return True

    return record


def test_back_to_back_steps_keep_rest(worker: tuple[BagelInputWorker, _Keys]) -> None:
    """上一步一松键就请求下一步，按下也要等停顿满。"""
    w, keys = worker
    for _ in range(4):
        _wait(lambda: not w.forward_step_active())
        assert w.step_forward(0.03)
    _wait(lambda: not w.forward_step_active())
    rests = keys.rests()
    assert len(rests) == 3
    assert min(rests) >= STEP_REST_SECONDS


def test_hold_then_step_releases_and_rests(worker: tuple[BagelInputWorker, _Keys]) -> None:
    """持续前进中改为短按：先松键，停顿满了才按下这一步。"""
    w, keys = worker
    w.hold_forward()
    _wait(lambda: w.forward_held_seconds() is not None)
    assert w.step_forward(0.03)
    _wait(lambda: not w.forward_step_active())
    assert [e[0] for e in keys.events] == ['down', 'up', 'down', 'up']
    assert keys.rests()[0] >= STEP_REST_SECONDS


def test_trusted_initial_ratio_lifts_warmup_cap_and_chunks_large_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无记忆首局受 45 度热身限幅；跨局可信起步值放开上限，<=120 度单段直发，>120 度拆两段连发。"""
    monkeypatch.setattr(input_worker, 'TURN_SAMPLE_INTERVAL', 0.002)
    monkeypatch.setattr(input_worker, 'TURN_CHUNK_INTERVAL', 0.0)
    monkeypatch.setattr(input_worker, 'TURN_POST_TURN_QUIET', 10.0)

    mem = input_worker.TurnRatioMemory()
    assert not mem.trusted
    mem.absorb(1.5, input_worker.TURN_HINT_MIN_SAMPLES, -19.0)
    assert mem.trusted and mem.hint == 1.5

    cold_sent: list[float] = []
    cold = input_worker._TurnWorker(_record_turn(cold_sent), lambda: 0.0, initial_ratio=1.0, trust_initial_ratio=False)
    try:
        cold.submit(150.0)
        _wait(lambda: len(cold_sent) >= 1)
        assert cold_sent == [pytest.approx(45.0)]
    finally:
        cold.stop()

    mid_sent: list[float] = []
    mid = input_worker._TurnWorker(
        _record_turn(mid_sent), lambda: 0.0, initial_ratio=mem.hint, trust_initial_ratio=mem.trusted,
    )
    try:
        mid.submit(110.0)
        _wait(lambda: len(mid_sent) >= 1)
        assert mid_sent == [pytest.approx(110.0 / 1.5)]
    finally:
        mid.stop()

    warm_sent: list[float] = []
    warm = input_worker._TurnWorker(
        _record_turn(warm_sent), lambda: 0.0, initial_ratio=mem.hint, trust_initial_ratio=mem.trusted,
    )
    try:
        warm.submit(150.0)
        _wait(lambda: len(warm_sent) >= 2)
        assert len(warm_sent) == 2
        assert sum(warm_sent) == pytest.approx(100.0)
    finally:
        warm.stop()


def test_chunked_sample_isolated_and_prior_plus_outlier_guard_protect_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拆段样本不写单段峰值箱；跨局先验权重防早局噪声劫持；偶发离群值忽略、连续 3 次放行。"""
    monkeypatch.setattr(input_worker._TurnWorker, '_loop', lambda self: None)
    tw = input_worker._TurnWorker(
        lambda _: True, lambda: 0.0, initial_ratio=1.5, trust_initial_ratio=True,
    )
    try:
        # 拆 2 段样本（倍率 1.7）：只消费放行，不污染单段比例，也不计入 turn_samples
        tw._pending = 80.0
        tw._pending_chunks = 2
        tw._anchor = 0.0
        assert tw._learn(0.0, 80.0, 136.0) is True
        assert tw.turn_ratio == pytest.approx(1.5)
        assert tw.turn_samples == 0

        # 跨局先验权重 3 票：2 个略偏高样本（1.90，在 1.5*1.4=2.1 内）仍不撼动 1.5
        for _ in range(2):
            tw._pending = 40.0
            tw._pending_chunks = 1
            tw._anchor = 0.0
            assert tw._learn(0.0, 40.0, 76.0) is True
        assert tw.turn_ratio == pytest.approx(1.5)
        assert tw.turn_samples == 2

        # 偶发离群样本（3.0 > 1.5*1.4=2.1）连续 2 次均被忽略，正常样本到达后清零离群计数
        for _ in range(2):
            tw._pending = 20.0
            tw._pending_chunks = 1
            tw._anchor = 0.0
            assert tw._learn(0.0, 20.0, 60.0) is True
        assert tw.turn_samples == 2
        tw._pending = 40.0
        tw._pending_chunks = 1
        tw._anchor = 0.0
        assert tw._learn(0.0, 40.0, 60.0) is True
        assert tw._outlier_streak == 0
        assert tw.turn_samples == 3
        assert tw._peak_count == input_worker.TURN_RATIO_PRIOR_WEIGHT + 1

        # 真实灵敏度变化到 2.5：前 2 次带外样本拦截，第 3 次起连续放行，累计超过先验（4 票）后切到 2.5
        for _ in range(6):
            tw._pending = 20.0
            tw._pending_chunks = 1
            tw._anchor = 0.0
            assert tw._learn(0.0, 20.0, 50.0) is True
        assert tw.turn_ratio == pytest.approx(1.5)
        tw._pending = 20.0
        tw._pending_chunks = 1
        tw._anchor = 0.0
        assert tw._learn(0.0, 20.0, 50.0) is True
        assert tw.turn_ratio == pytest.approx(2.5)
    finally:
        tw.stop()


def test_small_turn_releases_without_still_timeout_and_big_target_preempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """<8 度微调需有 >=0.5 度变化或满 0.4 秒宽限才放行；>=8 度转向宽限后遇 >=45 度新目标抢先放行。"""
    monkeypatch.setattr(input_worker._TurnWorker, '_loop', lambda self: None)
    now = [100.0]
    monkeypatch.setattr(input_worker.time, 'monotonic', lambda: now[0])
    tw = input_worker._TurnWorker(lambda _: True, lambda: 0.0)
    try:
        # <8 度小微调：宽限内画面未动仍等待，避免转稳前提前补转；满 0.4 秒宽限或已有变化即放行
        tw._pending = 3.0
        tw._pending_at = 100.0
        tw._anchor = 10.0
        now[0] = 100.2
        assert tw._learn(10.0, 3.0, 10.0) is False
        assert tw._learn(10.0, 3.0, 11.0) is True
        assert tw._pending is None

        tw._pending = 3.0
        tw._pending_at = 100.0
        tw._anchor = 10.0
        now[0] = 100.45
        assert tw._learn(10.0, 3.0, 10.0) is True
        assert tw._pending is None

        # >=8 度转向：宽限内仍等待；宽限过后无大目标进静止计时，遇 >=45 度新目标立刻放行
        tw._pending = 12.0
        tw._pending_at = 100.0
        tw._anchor = 10.0
        tw._target = 15.0
        now[0] = 100.2
        assert tw._learn(10.0, 12.0, 10.0) is False
        now[0] = 100.5
        assert tw._learn(10.0, 12.0, 10.0) is False
        assert tw._still_since == 100.5
        tw._target = 100.0
        assert tw._learn(10.0, 12.0, 10.0) is True
        assert tw._pending is None
    finally:
        tw.stop()


@pytest.mark.parametrize('results', [[False], [True, False], [RuntimeError('failed')], [True, RuntimeError('failed')]])
def test_incomplete_dispatch_discards_entire_sample(
    monkeypatch: pytest.MonkeyPatch, results: list[bool | Exception],
) -> None:
    monkeypatch.setattr(input_worker._TurnWorker, '_loop', lambda self: None)
    monkeypatch.setattr(input_worker, 'TURN_CHUNK_INTERVAL', 0.0)
    sent: list[float] = []
    remaining = iter(results)

    def handler(angle: float) -> bool:
        sent.append(angle)
        result = next(remaining)
        if isinstance(result, Exception):
            raise result
        return result

    tw = input_worker._TurnWorker(handler, lambda: 0.0, stationary_token=lambda: (4, 0))
    try:
        tw._target = 90.0
        tw._pending = -90.0
        tw._anchor = 10.0
        tw._pending_chunks = 2
        tw._pending_token = (4, 0)
        tw._still_since = 1.0
        tw._dispatch_turn(-90.0, 2 if len(results) == 2 else 1)
        expected = [-45.0, -45.0] if len(results) == 2 else [-90.0]
        assert sent == expected
        assert tw._pending is None and tw._anchor is None
        assert tw._pending_chunks == 1 and tw._pending_token is None and tw._still_since is None
        assert tw.turn_samples == 0 and tw.turn_ratio == 1.0 and tw._peak_bins == {}
    finally:
        tw.stop()


@pytest.mark.parametrize('interrupt', ['cancel', 'stop'])
def test_interrupted_dispatch_cannot_publish_partial_sample(
    monkeypatch: pytest.MonkeyPatch, interrupt: str,
) -> None:
    monkeypatch.setattr(input_worker._TurnWorker, '_loop', lambda self: None)
    monkeypatch.setattr(input_worker, 'TURN_CHUNK_INTERVAL', 0.0)
    sent: list[float] = []

    def handler(angle: float) -> bool:
        sent.append(angle)
        if interrupt == 'cancel':
            tw.submit(None)
        else:
            tw.stop()
        return True

    tw = input_worker._TurnWorker(handler, lambda: 0.0, stationary_token=lambda: (4, 0))
    try:
        tw._target = 90.0
        tw._pending, tw._anchor, tw._pending_token = 90.0, 0.0, (4, 0)
        tw._pending_chunks = 2
        tw._dispatch_turn(90.0, 2)
        assert sent == [45.0]
        assert tw._pending is None and tw._anchor is None and tw._pending_token is None
        assert tw._pending_chunks == 1 and tw.turn_samples == 0
    finally:
        tw.stop()


def _playcover_worker() -> tuple[BagelInputWorker, SimpleNamespace]:
    controller = SimpleNamespace(
        _touch_lock=threading.RLock(), _touch_held=False, _touch_owner=None,
        _touch_generation=10, touch_ui_enabled=True, dodge=Mock(),
    )
    worker = object.__new__(BagelInputWorker)
    worker.ctx = SimpleNamespace(controller=controller)
    worker._playcover_dodges = 0
    worker._stopping = False
    worker._action_epoch = 0
    worker._ensure_forward_down = Mock()
    return worker, controller


def test_movement_during_settling_discards_sample_then_clean_turn_learns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(input_worker._TurnWorker, '_loop', lambda self: None)
    worker, controller = _playcover_worker()
    tw = input_worker._TurnWorker(lambda _: True, lambda: 0.0, stationary_token=worker._stationary_token)
    try:
        tw._peak_bins = {0: 2}
        tw._peak_key = 0
        tw._peak_count = 2
        tw._outlier_streak = 2
        tw._pending, tw._anchor, tw._pending_token = -45.0, 0.0, worker._stationary_token()
        controller._touch_held = True
        controller._touch_owner = 'move'
        controller._touch_generation += 1
        assert worker._stationary_token() is None
        controller._touch_held = False
        controller._touch_owner = None
        controller._touch_generation += 1
        assert tw._learn(0.0, -45.0, -150.7) is True
        assert tw.turn_samples == 0 and tw.turn_ratio == 1.0
        assert tw._peak_bins == {0: 2} and tw._peak_key == 0 and tw._peak_count == 2
        assert tw._outlier_streak == 2
        assert tw._pending is None and tw._anchor is None and tw._pending_token is None
        tw._pending, tw._anchor, tw._pending_token = -45.0, 0.0, worker._stationary_token()
        assert tw._learn(0.0, -45.0, -90.0) is True
        assert tw.turn_samples == 1 and tw._peak_bins[20] == 1
        assert tw._pending is None and tw._pending_token is None
    finally:
        tw.stop()


def test_held_joystick_and_route_dodge_disqualify_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_worker._TurnWorker, '_loop', lambda self: None)
    monkeypatch.setattr(input_worker, 'sys', SimpleNamespace(platform='darwin'))
    worker, controller = _playcover_worker()
    tw = input_worker._TurnWorker(lambda _: True, lambda: 0.0, stationary_token=worker._stationary_token)
    try:
        controller._touch_held = True
        controller._touch_owner = 'move'
        tw._pending, tw._anchor, tw._pending_token = -45.0, 0.0, worker._stationary_token()
        controller._touch_held = False
        assert worker._stationary_token() is None
        controller._touch_owner = None
        assert tw._learn(0.0, -45.0, -90.0) is True
        assert tw.turn_samples == 0 and tw._pending is None

        tw._pending, tw._anchor, tw._pending_token = -45.0, 0.0, worker._stationary_token()
        worker._run_instant(input_worker._Command(input_worker.DODGE))
        assert worker._playcover_dodges == 1
        worker._ensure_forward_down.assert_called_once()
        controller.dodge.assert_called_once()
        assert tw._learn(0.0, -45.0, -90.0) is True
        assert tw.turn_samples == 0 and tw._pending is None
    finally:
        tw.stop()


def test_worker_uses_token_from_before_anchor_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_worker, 'TURN_POST_TURN_QUIET', 0.0)
    entered: queue.Queue[int] = queue.Queue()
    frames: queue.Queue[float] = queue.Queue()
    dispatched: queue.Queue[tuple[float, tuple[int, int] | None]] = queue.Queue()
    learned: queue.Queue[tuple[tuple[int, int] | None, bool]] = queue.Queue()
    generation = [10]
    count = 0

    def sample() -> float:
        nonlocal count
        count += 1
        entered.put(count)
        return frames.get(timeout=3)

    def handler(angle: float) -> bool:
        dispatched.put((angle, tw._pending_token))
        tw.submit(None)
        return True

    tw = input_worker._TurnWorker(handler, sample, stationary_token=lambda: (generation[0], 0))
    learn = tw._learn

    def track_learn(anchor: float, pending: float, stable: float) -> bool:
        token = tw._pending_token
        result = learn(anchor, pending, stable)
        learned.put((token, result))
        return result

    tw._learn = track_learn
    try:
        tw.submit(90.0)
        for index in range(3):
            entered.get(timeout=3)
            if index == 2:
                generation[0] += 2
            frames.put(0.0)
        angle, token = dispatched.get(timeout=3)
        assert angle == 45.0 and token == (10, 0)
        for _ in range(3):
            entered.get(timeout=3)
            frames.put(90.0)
        assert learned.get(timeout=3) == ((10, 0), True)
        assert tw.turn_samples == 0 and tw.turn_ratio == 1.0
        assert tw._pending is None and tw._pending_token is None
    finally:
        with tw._lock:
            tw._stopping = True
        frames.put(0.0)
        tw.stop()
