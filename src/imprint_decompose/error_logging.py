"""Save a compact incident report next to the standalone executable."""

from datetime import datetime
from pathlib import Path
import sys
import traceback


def save_error_report(error, *, context='', exc_info=None):
    try:
        root = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
                else Path(__file__).resolve().parents[2])
        log_dir = root / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        process_log = log_dir / 'imprint_decompose.log'
        recent = ''
        if process_log.exists():
            with process_log.open('rb') as stream:
                stream.seek(max(0, process_log.stat().st_size - 131072))
                recent = stream.read().decode('utf-8', errors='replace')
        trace = ''.join(traceback.format_exception(*exc_info)) if exc_info else ''
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        destination = log_dir / f'error_{stamp}.log'
        destination.write_text(
            f'时间: {datetime.now().isoformat()}\n错误: {error}\n{context}\n'
            f'{trace}\n--- 最近过程日志 ---\n{recent}',
            encoding='utf-8',
        )
        return destination
    except OSError:
        return None
