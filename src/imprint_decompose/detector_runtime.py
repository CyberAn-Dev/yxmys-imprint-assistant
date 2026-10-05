"""State-first analysis using the established visual signals and priorities."""
from types import MethodType

import cv2

from .models import ImprintFrameAnalysis, ImprintState


def analyze(self, frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    reward, overlay, thirds, reward_ready = self._reward_signal(hsv)
    confirm, button, parchment, confirm_ready = self._confirm_signal(hsv)
    detail, detail_score, visible, surface, dismantle_ready = self._detail_signal(hsv)
    list_score, count, cards = self._list_signal(hsv)
    if reward_ready:
        state, reason = ImprintState.REWARD, '检测到分解后的恭喜获得奖励遮罩'
    elif confirm_ready:
        state, reason = ImprintState.CONFIRM, '检测到分解确认弹窗与红色确定按钮'
    elif visible:
        state = ImprintState.DETAIL
        reason = ('检测到刻印双卡、羊皮纸面板和元素槽位' if detail.right_ready else
                  '检测到详情弹窗，但右卡元素槽位尚未达到安全识别条件')
    elif count > 0 and list_score >= .05:
        state, reason = ImprintState.LIST, f'检测到刻印列表卡片 {count} 张'
    else:
        state, reason = ImprintState.UNKNOWN, '未同时满足刻印列表/详情/确认的视觉条件'

    # Equipped artwork on the list is NOT a red percentage. In particular,
    # do not load/run the OCR network before the first real detail is opened.
    # Never reuse an older detail's OCR values for a new frame.
    red_score, red_count, values, texts, unreadable = (0., 0, (), (), 0)
    if state == ImprintState.DETAIL:
        red_score, red_count, values, texts, unreadable = self._red_attribute_signal(hsv)
    return ImprintFrameAnalysis(
        state=state, detail=detail, detail_score=detail_score,
        confirm_score=confirm, list_score=list_score, list_card_count=count,
        confirm_ready=confirm_ready, dismantle_ready=dismantle_ready, reason=reason,
        dismantle_surface_score=surface, confirm_button_red_ratio=button,
        confirm_dialog_parchment_ratio=parchment, reward_score=reward,
        reward_overlay_ratio=overlay, reward_min_thirds_ratio=thirds,
        reward_ready=reward_ready, red_attribute_score=red_score,
        red_attribute_count=red_count, red_attribute_values=values,
        red_attribute_texts=texts, red_attribute_unreadable_count=unreadable,
        list_cards=cards)


def install_state_first_analysis(detector):
    # Install before the OCR wrapper, which supplies its current BGR frame.
    detector.analyze = MethodType(analyze, detector)
