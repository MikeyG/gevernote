from PySide.QtGui import QIcon
import os
import sys
from everpad.tools import resource_filename


def get_icon():
    return QIcon.fromTheme('everpad', QIcon('../../everpad.png'))


def get_file_icon_path():
    """
    Get path of icon for file
    foe embedding in html.
    """
    paths = (
        os.path.join(
            os.path.dirname(__file__),
            '../../data/everpad-file.png',
        ),
        resource_filename('share/icons/hicolor/48x48/actions/everpad-file.png'),
        '/usr/local/share/icons/hicolor/48x48/actions/everpad-file.png',
        '/usr/share/icons/hicolor/48x48/actions/everpad-file.png',
    )
    for path in paths:
        if os.path.isfile(path):
            return 'file://%s' % path
file_icon_path = get_file_icon_path()
