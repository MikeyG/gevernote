from everpad.provider.service import ProviderService
from everpad.provider.sync.agent import SyncThread
from everpad.provider.tools import get_db_session
from everpad.specific import AppClass
from everpad.tools import print_version
#import everpad.provider.tools
import everpad.provider.models
from everpad.provider.enauth import get_auth_token,change_auth_token,delete_auth_token 

from PySide.QtCore import Slot, QSettings

# do I need full dbus? MKG
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop

import signal
import fcntl
import os
import getpass
import argparse
import sys

# python built-in logging 
import logging

# daemon.py Main everpad-provider file - started everpad-provider call to main( )


class ProviderApp(AppClass):

    def __init__(self, verbose, *args, **kwargs):

        # non-kde:
        # from PySide.QtCore import QCoreApplication    
        # AppClass = QCoreApplication
        AppClass.__init__(self, *args, **kwargs)

        # ref:  http://qt-project.org/doc/qt-4.8/qsettings.html
        #
        # For example, if your product is called Star Runner and your company 
        # is called MySoft, you would construct the QSettings object as follows:
        #     QSettings settings("MySoft", "Star Runner");
        #  Backwards?
        self.settings = QSettings('everpad', 'everpad-provider')

        # debug output - true if verbose passed
        self.verbose = verbose

        # Ref: http://excid3.com/blog/an-actually-decent-python-dbus-tutorial/
        # SessionBus because service is a session level daemon
        session_bus = dbus.SessionBus()
        self.bus = dbus.service.BusName("com.everpad.Provider", session_bus)
        self.service = ProviderService(session_bus, '/EverpadProvider')

        # subclass PySide.QtCore.QThread  - agent.py
        # setup Sync thread
        self.sync_thread = SyncThread()

        # connect Sync thread sync_state_changed
        self.sync_thread.sync_state_changed.connect(
            Slot(int)(self.service.sync_state_changed),
        )

        # connect Sync thread data_changed
        self.sync_thread.data_changed.connect(
            Slot()(self.service.data_changed),
        )
        
        # Start Sync Thread if provider is authenticated
        if get_auth_token( ):
            print("Auth token")
            self.sync_thread.start()
        else:
            print("No auth token")

        # ************************************************************
        #    Authentication and Termination Signals Setup
        # ************************************************************

        # provider_authenticate @Slot
        self.service.qobject.authenticate_signal.connect(
            self.provider_authenticate,
        )
        # on_authenticated @Slot
        self.service.qobject.authenticate_signal.connect(
            self.on_authenticated,
        )
        # on_remove_authenticated @Slot
        self.service.qobject.remove_authenticate_signal.connect(
            self.on_remove_authenticated,
        )
        self.service.qobject.terminate.connect(self.terminate)

        # ************************************************************
        #                   Configure logger
        # ************************************************************
        # https://docs.python.org/2/library/logging.html
        # good ref: 
        # http://victorlin.me/posts/2012/08/26/good-logging-practice-in-python
        # Yes, quite drawn out with all my if verbose, but readable for me when 
        # I come back to this in a couple weeks or more
        logging.basicConfig(level=logging.INFO)
        
        # create logger and set to debug
        self.logger = logging.getLogger('gevernote-provider')
        logger.setLevel(logging.DEBUG)
        
        fh = logging.FileHandler(
            os.path.expanduser('~/.everpad/logs/gevernote-provider.log'))
        fh.setLevel(logging.DEBUG)
        if verbose:
            ch = logging.StreamHandler( )
            ch = setLevel(logging.DEBUG)

        fhformatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(fhformatter)
        if verbose:
            chformatter('%(asctime)s - %(message)s')
            ch.setFormatter(chformatter)

        self.logger.addHandler(fh)
        if verbose:
            self.logger.addHandler(ch)

        # self.logger.info('Provider started.')
        #log('Provider started.')
        #log("sqlalchemy %s" % sqlalchemy.__version__)

    # ************************************************************
    #          Authentication and Termination 
    # ************************************************************

    # add auth MKG
    @Slot(str)
    def provider_authenticate(self):
        # auth_geverpad_token enauth.py
        change_auth_token( ) 
        if get_auth_token( ) != "None":
            self.sync_thread.start( )

    @Slot(str)
    def on_authenticated(self, token):
        # set_auth_token everpad/provider/tools.py 
        #set_auth_token(token)
        self.sync_thread.start()

    @Slot()
    def on_remove_authenticated(self):
        print("Remove Auth")

        self.sync_thread.timer.stop()
        self.sync_thread.quit()
        self.sync_thread.update_count = 0

        # delete_auth_token - enauth.py        
        delete_auth_token( )
        
        # get_db_sesson everpad/provider/tools.py        
        session = get_db_session()

        session.query(everpad.provider.models.Note).delete(
            synchronize_session='fetch',
        )
        session.query(everpad.provider.models.Resource).delete(
            synchronize_session='fetch',
        )
        session.query(everpad.provider.models.Notebook).delete(
            synchronize_session='fetch',
        )
        session.query(everpad.provider.models.Tag).delete(
            synchronize_session='fetch',
        )
        session.commit()

    # Handles verbose option to output data to console
    # in addition to file
    def log(self, data):
        self.logger.debug(data)
        #if self.verbose:
        #    print data

    # stop SyncThread 
    @Slot()
    def terminate(self):
        self.sync_thread.quit()
        self.quit()


# creates everpad directories - called from main( )
def _create_dirs(dirs):
    """Create everpad dirs"""
    for path in dirs:
        try:
            os.mkdir(os.path.expanduser(path))
        except OSError:
            continue


# kicks things off starting everpad-provider
def main():
    # ctrl-c terminates the process gracefully
    # http://docs.python.org/dev/library/signal.html
    # http://pymotw.com/2/signal/
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    # create everpad directories - _create_dirs local
    _create_dirs(['~/.everpad/', '~/.everpad/data/', '~/.everpad/logs/'])

    # parse args using funky python built-in stuff
    # {none}, verbose, or version
    # ref: http://docs.python.org/2/howto/argparse.html
    parser = argparse.ArgumentParser()
    parser.add_argument('--verbose', action='store_true', help='verbose output')
    parser.add_argument('--version', '-v', action='store_true', help='show version')
    args = parser.parse_args(sys.argv[1:])

    # print version (tools.py) and exit. print_version executes sys.exit(0)
    # after printing
    if args.version:
        print_version()

    # lockfile using usr name getpass.getuser()
    # start main loop or error out
    fp = open('/tmp/gverpad-provider-%s.lock' % getpass.getuser(), 'w')

    try:

        fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)

        # ref: http://dbus.freedesktop.org/doc/dbus-python/api/
        # set_as_default is given and is true, set the new main 
        # loop as the default for all new Connection or Bus instances
        # allows this script to receive DBus calls
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

        # SlavikZ - Fix occasional everpad-provider segfaults 050814
        # http://dbus.freedesktop.org/doc/dbus-python/api/dbus.mainloop.glib-module.html
        # Initialize threads in dbus-glib, if this has not already been done.
        dbus.mainloop.glib.threads_init()

        # http://stackoverflow.com/questions/22390064/use-dbus-to-just-send-a-message-in-python
        app = ProviderApp(args.verbose, sys.argv)
        app.exec_()

    except IOError:
        print("geverpad-provider already running")
    except Exception as e:
        print(e)

# allows running daemon.py directly
if __name__ == '__main__':
    main()
