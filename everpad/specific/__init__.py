from PySide.QtGui import QIcon
from everpad.specific.unity.launcher import UnityLauncher
import os


# This seems to handle all desktop related differences 


launchers = {
    'ubuntu': UnityLauncher,
    'default': UnityLauncher,
}

# I don't really get the launcher stuff. As I understand:
#   os.environ.get(KEY_TO_GET, Default_return_if_KEY_None) 
def get_launcher(*args, **kwargs):
    de = os.environ.get('DESKTOP_SESSION', 'default')
    launcher = launchers.get(de, launchers['default'])
    return launcher(*args, **kwargs)


# Set the everpad icon based on session
def get_tray_icon(is_black=False):
    if os.environ.get('DESKTOP_SESSION', 'default') == 'gnome':
        return QIcon.fromTheme('everpad', QIcon('../../data/everpad.png'))
    if is_black:
        return QIcon.fromTheme('everpad-black', QIcon('../../data/everpad-black.png'))
    else:
        return QIcon.fromTheme('everpad-mono', QIcon('../../data/everpad-mono.png'))


if 'kde' in os.environ.get('DESKTOP_SESSION', '') or os.environ.get('KDE_FULL_SESSION') == 'true':  # kde init qwidget for wallet access
    from PySide.QtGui import QApplication
    AppClass = QApplication
else:
    from PySide.QtCore import QCoreApplication
    AppClass = QCoreApplication

# Handle keyring for Lubuntu and LXDE
class QSettingsKeyringAdpdater(object):
    def __init__(self, settings):
        self._settings = settings

    def _prepare_name(self, app, name):
        return '%s_%s' % (app, name)

    def set_password(self, app, name, password):
        self._settings.setValue(self._prepare_name(app, name), password)

    def get_password(self, app, name):
        self._settings.value(self._prepare_name(app, name))

# get keyring - called from tools.py
def get_keyring():
    if os.environ.get('DESKTOP_SESSION', 'default') in ('Lubuntu', 'LXDE'):
        # keyring fails on initialisation in lxde
        return QSettingsKeyringAdpdater(AppClass.instance().settings)
    else:
        import keyring
        return keyring
