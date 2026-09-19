import sys
import threading
import logging
from pathlib import Path

from imprint_decompose.error_logging import save_error_report


_original_exception_hook = sys.excepthook
_original_thread_hook = threading.excepthook


def _record_exception(error_type, error, trace):
    save_error_report(error, context='主线程异常',
                      exc_info=(error_type, error, trace))
    _original_exception_hook(error_type, error, trace)


def _record_thread_exception(args):
    save_error_report(args.exc_value, context=f'线程异常: {args.thread.name}',
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    _original_thread_hook(args)


sys.excepthook = _record_exception
threading.excepthook = _record_thread_exception

from imprint_decompose import app
from imprint_decompose.controller_v2 import ImprintDecomposeController
from imprint_decompose.ui_v2 import ImprintDecomposeUI

app.ImprintDecomposeUI = ImprintDecomposeUI
app.ImprintDecomposeController = ImprintDecomposeController


def _logs_dir():
    root = (Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[1])
    return root / 'logs'


def _process_logs():
    root = _logs_dir()
    return (root / 'imprint_decompose.log', root / 'tower_bot.log')


def main(argv=None):
    log_dir = _logs_dir()
    existing_errors = set(log_dir.glob('error_*.log')) if log_dir.exists() else set()
    for path in _process_logs():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    try:
        result = app.main(argv)
    except SystemExit as exit_event:
        result = exit_event.code
    new_errors = set(log_dir.glob('error_*.log')) - existing_errors if log_dir.exists() else set()
    if result in (None, 0) and not new_errors:
        logging.shutdown()
        for path in _process_logs():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return result


if __name__ == "__main__":
    main()
