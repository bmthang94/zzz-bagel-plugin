"""PlayCover 的前台水平镜头手势。"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from one_dragon.base.geometry.point import Point

if TYPE_CHECKING:
    from zzz_od.controller.zzz_pc_controller import ZPcController

# 实测手势的横向 HID 报告；短转只取前缀，不放慢整段。
_STEPS = (20, 9, 12, 6, 13, 10, 19, 10, 12, 13, 13, 36, 15, 20, 20, 17, 15, 5, 15, 12, 9, 5, 4, 2)
_TIMES = (
    0, .008, .016, .024, .032, .040, .050, .056, .066, .072, .084, .090,
    .098, .106, .116, .123, .130, .138, .149, .154, .166, .170, .180, .186,
)


def flick_camera(controller: ZPcController, distance: float) -> bool:
    """在前台且触点空闲时，用水平拖动转动镜头。"""
    if not math.isfinite(distance) or distance == 0:
        return False
    with controller._touch_lock:
        win = controller.game_win
        if controller._touch_held or controller._touch_owner is not None or not win.is_win_active:
            return False
        left = win.game2win_pos(Point(480, 459))
        right = win.game2win_pos(Point(1440, 459))
        if left is None or right is None:
            return False
        width = right.x - left.x
        if width <= 0:
            return False
        sign = 1 if distance > 0 else -1
        span = min(abs(distance) * width / 960, .70 * width, 415.)
        counts = max(1, min(sum(_STEPS), round(span * .75)))
        start_x = round((left.x + right.x) / 2 - sign * (span / 2 + 20))
        mouse = controller._get_mac_virtual_hid_mouse()
        import pyautogui

        pyautogui.moveTo(start_x, left.y, _pause=False)
        mouse.nudge_cursor()
        time.sleep(.05)
        for dx in (2, 1, 3, 2, 4, 3):
            if not win.is_win_active:
                return False
            mouse.move_relative(sign * dx, 0)
            time.sleep(.008)
        if not win.is_win_active:
            return False
        try:
            mouse.press('left')
            t = time.monotonic() + .005
            for dt, dx in zip(_TIMES, _STEPS, strict=False):
                time.sleep(max(0, t + dt - time.monotonic()))
                if not win.is_win_active:
                    return False
                step = min(dx, counts)
                mouse.move_relative(sign * step, 0)
                counts -= step
                if counts == 0:
                    break
            time.sleep(max(0, t + .202 - time.monotonic()))
        finally:
            mouse.release('left')
        return bool(win.is_win_active)
