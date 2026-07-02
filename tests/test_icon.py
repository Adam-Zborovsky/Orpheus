import os
import struct

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from orpheus.ui.tray import ICON_PATH, app_icon


def test_icon_asset_is_valid_multisize_ico():
    data = ICON_PATH.read_bytes()
    reserved, ico_type, count = struct.unpack("<HHH", data[:6])
    assert (reserved, ico_type) == (0, 1)
    assert count >= 5  # multi-size: at least 16..256 variants


def test_app_icon_loads():
    import PySide6.QtWidgets as w

    app = w.QApplication.instance() or w.QApplication([])
    icon = app_icon()
    assert not icon.isNull()
    assert icon.pixmap(32, 32).width() > 0
