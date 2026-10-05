"""Normal exit must preserve bounded logs, without starting the app."""
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from imprint_decompose import logging_utils
from tower_bot import logger as common_logging


class LogRetentionTests(unittest.TestCase):
    def test_entry_preserves_logs_on_exit_and_restart(self):
        # Entry installs exception hooks on import; do not leave them in tests.
        with patch.object(sys, 'excepthook'), patch.object(threading, 'excepthook'):
            import imprint_decompose_entry as entry
        with tempfile.TemporaryDirectory() as folder:
            paths = tuple(Path(folder) / name for name in ('tower_bot.log', 'imprint_decompose.log'))
            for path in paths:
                path.write_text('previous diagnostic\n', encoding='utf8')
            with patch.object(entry, '_logs_dir', return_value=Path(folder)), \
                 patch.object(entry.app, 'main', return_value=0), \
                 patch.object(entry.app, 'setup_logging'), \
                 patch.object(entry.app, 'setup_imprint_logging'), \
                 patch.object(entry.logging, 'getLogger', return_value=Mock(handlers=[])) as get_logger:
                for _ in range(2):
                    self.assertEqual(entry.main([]), 0)
                    self.assertTrue(all(p.exists() for p in paths))
                    self.assertTrue(all('previous diagnostic' in p.read_text(encoding='utf8') for p in paths))
                self.assertIn('RUN_START', str(get_logger.return_value.info.call_args_list))
                self.assertIn('RUN_END', str(get_logger.return_value.info.call_args_list))

    def test_log_flush_failure_does_not_hide_application_error(self):
        with patch.object(sys, 'excepthook'), patch.object(threading, 'excepthook'):
            import imprint_decompose_entry as entry
        handler = Mock()
        handler.flush.side_effect = OSError('offline disk failure')
        logger = Mock(handlers=[handler])
        with patch.object(entry.app, 'main', side_effect=RuntimeError('original app error')), \
             patch.object(entry.app, 'setup_logging'), \
             patch.object(entry.app, 'setup_imprint_logging'), \
             patch.object(entry.logging, 'getLogger', return_value=logger):
            with self.assertRaisesRegex(RuntimeError, 'original app error'):
                entry.main([])
        logger.exception.assert_called_once()
        handler.flush.assert_called()

    def test_both_loggers_rotate_instead_of_growing_forever(self):
        for module, setup, name in (
                (common_logging, common_logging.setup_logging, 'tower_bot.log'),
                (logging_utils, logging_utils.setup_imprint_logging, 'imprint_decompose.log')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                isolated = logging.Logger('offline.retention')
                with patch.object(module, '_CONFIGURED', False), \
                     patch.object(module.logging, 'getLogger', return_value=isolated), \
                     patch.object(module, 'logs_dir', return_value=Path(folder)):
                    setup()
                try:
                    files = [h for h in isolated.handlers if isinstance(h, logging.FileHandler)]
                    self.assertEqual(len(files), 1)
                    handler = files[0]
                    self.assertEqual(handler.maxBytes, 1_000_000)
                    self.assertEqual(handler.backupCount, 1)
                    # Exercise the actual rotation handler with a smaller cap.
                    handler.maxBytes = 256
                    for index in range(20):
                        handler.handle(logging.makeLogRecord({'msg': f'record {index}: ' + 'x' * 80}))
                    handler.flush()
                    self.assertEqual({p.name for p in Path(folder).iterdir()}, {name, name + '.1'})
                    self.assertIn('record 19:', (Path(folder) / name).read_text(encoding='utf8'))
                    self.assertLessEqual(sum(p.stat().st_size for p in Path(folder).iterdir()), 512)
                finally:
                    for handler in isolated.handlers:
                        handler.close()


if __name__ == '__main__':
    unittest.main()
