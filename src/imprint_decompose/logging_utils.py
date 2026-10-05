"""Bounded persistent process diagnostics, shared by source and EXE runs."""
import logging
from logging.handlers import RotatingFileHandler

from tower_bot.config import logs_dir
from tower_bot.logger import LOG_BACKUPS, MAX_LOG_BYTES

_CONFIGURED = False


def setup_imprint_logging():
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger('imprint_decompose')
    logger.setLevel(logging.DEBUG)
    try:
        path = logs_dir() / 'imprint_decompose.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=MAX_LOG_BYTES,
                                      backupCount=LOG_BACKUPS, encoding='utf-8')
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(handler)
        logger.info('刻印详细日志已启用: %s', path)
    except OSError as exc:
        logger.warning('刻印详细日志目录不可写，仅保留主日志输出: %s', exc)
    _CONFIGURED = True
