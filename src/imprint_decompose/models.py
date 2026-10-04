"""Data models shared by the vision detector and the user interface.

The detector returns slots in their physical left-to-right order.  That order
is meaningful while showing the current imprint, so ``DetailAnalysis`` keeps
it for its display label.  Canonical ordering remains available for aggregate
statistics and combination matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from itertools import combinations_with_replacement
from typing import Iterable, Optional


class ImprintState(Enum):
    UNKNOWN = auto()
    LIST = auto()
    DETAIL = auto()
    CONFIRM = auto()
    REWARD = auto()


ELEMENT_ORDER = ('风暴', '烈焰', '电弧', '暗影', '大地')
ELEMENT_INDEX = {element: index for index, element in enumerate(ELEMENT_ORDER)}
ELEMENT_PAIR_COMBINATIONS = tuple(
    combinations_with_replacement(ELEMENT_ORDER, 2)
)


def canonical_combination(elements: Iterable[str]) -> tuple[str, ...]:
    """Return a stable order for counting equivalent combinations."""
    values = tuple(str(element) for element in elements if element)
    return tuple(
        sorted(
            values,
            key=lambda element: (ELEMENT_INDEX.get(element, len(ELEMENT_ORDER)), element),
        )
    )


@dataclass
class SlotReading:
    index: int
    active: bool
    element: Optional[str]
    active_ratio: float
    hue: Optional[float]
    confidence: float


@dataclass
class ListCardAnalysis:
    center_x: int
    center_y: int
    slots: tuple[SlotReading, ...]
    combination: tuple[str, ...]
    filled_count: int
    ready: bool
    slot_y: int = 0

    @property
    def label(self) -> str:
        if not self.combination:
            return '-'
        return combination_label(canonical_combination(self.combination))


@dataclass
class DetailAnalysis:
    left_slots: tuple[SlotReading, ...]
    right_slots: tuple[SlotReading, ...]
    left_star_score: float
    right_star_score: float
    left_panel_score: float
    right_panel_score: float
    right_combination: tuple[str, ...]
    right_filled_count: int
    right_ready: bool
    left_slot_y: int = 0
    right_slot_y: int = 0

    @property
    def right_has_unknown_element(self) -> bool:
        return any(slot.active and slot.element is None for slot in self.right_slots)

    @property
    def right_label(self) -> str:
        if not self.right_combination:
            return '-'
        # Preserve the actual slot order for the current-imprint panel.
        return combination_label(self.right_combination)


@dataclass
class ImprintFrameAnalysis:
    state: ImprintState
    detail: Optional[DetailAnalysis]
    detail_score: float
    confirm_score: float
    list_score: float
    list_card_count: int
    confirm_ready: bool
    dismantle_ready: bool
    reason: str
    dismantle_surface_score: float = 0.0
    confirm_button_red_ratio: float = 0.0
    confirm_dialog_parchment_ratio: float = 0.0
    reward_score: float = 0.0
    reward_overlay_ratio: float = 0.0
    reward_min_thirds_ratio: float = 0.0
    reward_ready: bool = False
    red_attribute_score: float = 0.0
    red_attribute_count: int = 0
    red_attribute_values: tuple[float, ...] = ()
    red_attribute_texts: tuple[str, ...] = ()
    red_attribute_unreadable_count: int = 0
    list_cards: tuple[ListCardAnalysis, ...] = ()


def combination_label(elements: Iterable[str]) -> str:
    return ' + '.join(elements) if elements else '-'


@dataclass
class ImprintStats:
    program_status: str = '已停止'
    window_status: str = '未找到'
    window_size: str = '-'
    window_advice: str = '尚未检测到小游戏窗口'
    vision_state: str = 'UNKNOWN'
    current_combination: str = '-'
    current_filled_slots: str = '-'
    total_decomposed: int = 0
    element_counts: dict[str, int] = field(
        default_factory=lambda: {element: 0 for element in ELEMENT_ORDER}
    )
    combination_counts: dict[str, int] = field(default_factory=dict)
    detail_score: float = 0.0
    confirm_score: float = 0.0
    list_score: float = 0.0
    dismantle_surface_score: float = 0.0
    confirm_button_red_ratio: float = 0.0
    confirm_dialog_parchment_ratio: float = 0.0
    reward_score: float = 0.0
    reward_overlay_ratio: float = 0.0
    reward_min_thirds_ratio: float = 0.0
    red_attribute_score: float = 0.0
    red_attribute_count: int = 0
    red_attribute_threshold: float = 8.0
    red_attribute_values: str = '-'
    red_attribute_unreadable_count: int = 0
    enhancement_enabled: bool = False
    enhancement_target: int = 1
    enhancement_completed: int = 0
    enhancement_clicks: int = 0
    enhancement_kept: int = 0
    total_kept: int = 0
    enhancement_status: str = '关闭'
    confirmation_mode: str = '自动确认'
    auto_mode: bool = False
    auto_filter: str = '未选择组合，全部处理'
    auto_candidates: int = 0
    auto_scrolled: int = 0
    last_action: str = '-'
    operation_history: str = '-'
    last_error: str = '-'
