"""Status presentation and completion events, without creating a window."""
import unittest
from unittest.mock import patch

from imprint_decompose.models import ImprintStats
from imprint_decompose.ui_v2 import ImprintDecomposeUI


class UIStatusTests(unittest.TestCase):
    def setUp(self):
        self.ui = object.__new__(ImprintDecomposeUI)
        self.ui.root = object()
        self.ui._last_completion_id = 0

    def test_completion_popup_exactly_once_and_again_for_new_completed_run(self):
        stats = ImprintStats(auto_completion_id=1, auto_phase='全部完成',
                             program_status='自动处理完成', auto_processed=17)
        with patch('imprint_decompose.ui_v2.messagebox.showinfo') as popup:
            self.ui._notify_completion(stats)
            self.ui._notify_completion(stats)
            popup.assert_called_once()
            self.assertIn('完成刻印自动筛选分解', popup.call_args.args[1])
            stats.auto_completion_id = 2
            self.ui._notify_completion(stats)
            self.assertEqual(popup.call_count, 2)

    def test_no_success_popup_on_protection_pause_or_material_failure(self):
        with patch('imprint_decompose.ui_v2.messagebox.showinfo') as popup:
            for phase in ('保护暂停', '已暂停', '已停止', '等待强化'):
                self.ui._notify_completion(ImprintStats(auto_phase=phase))
            self.ui._notify_completion(ImprintStats(auto_phase='保护暂停', auto_completion_id=1))
            popup.assert_not_called()

    def test_status_is_readable_bounded_and_does_not_leak_auto_safe_prefix(self):
        stats = ImprintStats(last_action='AUTO_SAFETY_STOP 当前属性与本轮记录不一致')
        self.assertEqual(self.ui._status_text(stats), '安全暂停 · 当前属性与本轮记录不一致')
        stats.last_error = '测试很长的错误' * 100
        stats.auto_phase = '保护暂停'
        self.assertLessEqual(len(self.ui._status_text(stats)), 84)
        stats.auto_phase = '手动选择'
        stats.last_action = '等待选卡'
        self.assertEqual(self.ui._status_text(stats), '等待选择')
