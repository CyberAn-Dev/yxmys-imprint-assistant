import sys
import threading
import logging

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
from imprint_decompose import __version__
from imprint_decompose.controller_v2 import ImprintDecomposeController
from imprint_decompose.ui_v2 import ImprintDecomposeUI
from imprint_decompose.hotkeys import ImprintHotkeyBridge

app.ImprintDecomposeUI = ImprintDecomposeUI
app.ImprintDecomposeController = ImprintDecomposeController
app.ImprintHotkeyBridge = ImprintHotkeyBridge


def _logs_dir():
    from tower_bot.config import logs_dir
    return logs_dir()


def main(argv=None):
    # A visible UI fault need not raise an exception. Keep process logs even
    # on normal exit/restart; the handlers enforce size/backup limits.
    app.setup_logging()
    app.setup_imprint_logging()
    logger = logging.getLogger('imprint_decompose')
    logger.info('RUN_START version=%s logs=%s', __version__, _logs_dir())
    result = None
    try:
        try:
            result = app.main(argv)
        except SystemExit as exit_event:
            result = exit_event.code
        logger.info('RUN_END version=%s result=%s', __version__, result)
        return result
    except BaseException:
        logger.exception('RUN_ABORT version=%s', __version__)
        raise
    finally:
        for target in (logging.getLogger(), logger):
            for handler in target.handlers:
                try:
                    handler.flush()
                except OSError:
                    pass  # Log I/O must not hide the original application error.


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == '--self-test':
        from imprint_decompose.diagnostics import run
        sys.exit(run(sys.argv[2]))
    sys.exit(main())
