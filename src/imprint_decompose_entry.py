import sys
import threading

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
main = app.main


if __name__ == "__main__":
    main()
