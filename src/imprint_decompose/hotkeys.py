"""Readable hotkey bridge, including the emergency Escape binding."""
from tower_bot.logger import get_logger

logger = get_logger(__name__)


class ImprintHotkeyBridge:
    def __init__(self, controller):
        self.controller = controller
        self._listener = None

    def start(self):
        try:
            from pynput import keyboard
            cfg = self.controller.feature_cfg['hotkeys']
            mapping = {}
            bindings = [('start', self.controller.start), ('stop', self.controller.stop),
                        ('debug', self.controller.toggle_debug),
                        ('emergency_pause', lambda: self.controller.emergency_pause('Esc 紧急暂停'))]
            for name, callback in bindings:
                key = str(cfg.get(name, '')).strip().lower()
                if key in ('esc', 'escape', '<escape>'):
                    key = '<esc>'
                if key:
                    if key in mapping:
                        raise ValueError('热键重复: ' + key)
                    mapping[key] = callback
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.start()
            logger.info('刻印热键已注册: %s', ', '.join(mapping))
            return True
        except Exception:
            logger.exception('热键注册失败，请使用界面按钮')
            self._listener = None
            return False

    def stop(self):
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
