from imprint_decompose import app
from imprint_decompose.controller_v2 import ImprintDecomposeController
from imprint_decompose.ui_v2 import ImprintDecomposeUI

app.ImprintDecomposeUI = ImprintDecomposeUI
app.ImprintDecomposeController = ImprintDecomposeController
main = app.main


if __name__ == "__main__":
    main()
