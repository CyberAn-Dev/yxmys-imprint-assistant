"""Application lifecycle shared by source and packaged entry points."""
import argparse
from pathlib import Path

from tower_bot.logger import get_logger, setup_logging
from .config import load_feature_config
from .controller_v2 import ImprintDecomposeController
from .hotkeys import ImprintHotkeyBridge
from .logging_utils import setup_imprint_logging
from .ui_v2 import ImprintDecomposeUI

logger = get_logger(__name__)


def build_parser():
    parser = argparse.ArgumentParser(description='英雄没有闪——刻印快速筛选分解小助手')
    parser.add_argument('--config', type=Path, default=None)
    parser.add_argument('--feature-config', type=Path, default=None)
    parser.add_argument('--dry-run', action='store_true', help='只识别，不发送鼠标输入')
    parser.add_argument('--no-gui', action='store_true')
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--no-hotkeys', action='store_true')
    return parser


def run_app(argv=None):
    setup_logging()
    setup_imprint_logging()
    args = build_parser().parse_args(argv)
    cfg, feature = load_feature_config(args.feature_config, root_config_path=args.config)
    if args.debug:
        cfg.setdefault('debug', {})['enabled'] = True
    controller = ImprintDecomposeController(cfg, feature, dry_run=args.dry_run)
    hotkeys = None
    try:
        if not args.no_hotkeys:
            hotkeys = ImprintHotkeyBridge(controller)
            if not hotkeys.start() and args.no_gui:
                raise RuntimeError('无界面模式无法注册停止热键')
        if args.no_gui:
            controller.start()
            if not controller.enabled.is_set():
                return 1
            while not controller.stopping.is_set():
                controller.join(timeout=0.5)
        else:
            ImprintDecomposeUI(controller).run()
    finally:
        if hotkeys:
            hotkeys.stop()
        controller.shutdown()
    return 0


def main(argv=None):
    try:
        return run_app(argv)
    except KeyboardInterrupt:
        return 0
    except Exception:
        logger.exception('刻印助手启动失败')
        raise
