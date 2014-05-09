from .service import ProviderService
from .sync.agent import SyncThread
from .tools import set_auth_token, get_auth_token, auth_geverpad_token, get_db_session
from ..specific import AppClass
from ..tools import print_version
from . import models
from PySide.QtCore import Slot, QSettings
import dbus
from dbus.mainloop.glib import DBusGMainLoop
# import dbus.mainloop.glib
import signal
import fcntl
import os
import getpass
import argparse
import sys
import logging

# Main everpad-provider file - started everpad-provider call to main( )

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
        if get_auth_token():
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
        
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger('everpad-provider')
        
        print("pre logging.getLogger")
    
        self.logger.setLevel(logging.DEBUG)
        print("pre logging.getLogger1")        
        fh = logging.FileHandler(
            os.path.expanduser('~/.everpad/logs/everpad-provider.log'))
        fh.setLevel(logging.DEBUG)
        print("pre logging.getLogger2")        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh.setFormatter(formatter)
        
        self.logger.addHandler(fh)
        self.logger.debug('Provider started.')

    # ************************************************************
    #          Authentication and Termination 
    # ************************************************************
       
    # add auth MKG
    @Slot(str)
    def provider_authenticate(self):
        # auth_geverpad_token everpad/provider/tools.py 
        oauth_result = auth_geverpad_token()
        
    # get_auth_token --- see tools.py
        
    @Slot(str)
    def on_authenticated(self, token):
        # set_auth_token everpad/provider/tools.py 
        set_auth_token(token)
        self.sync_thread.start()

    @Slot()
    def on_remove_authenticated(self):
    	  print("Remove Auth")
    	self.sync_thread.timer.stop()
        self.sync_thread.quit()
        self.sync_thread.update_count = 0

        # get_db_sesson everpad/provider/tools.py
        set_auth_token('')
        session = get_db_session()
        
        session.query(models.Note).delete(
            synchronize_session='fetch',
        )
        session.query(models.Resource).delete(
            synchronize_session='fetch',
        )
        session.query(models.Notebook).delete(
            synchronize_session='fetch',
        )
        session.query(models.Tag).delete(
            synchronize_session='fetch',
        )
        session.commit()

    def log(self, data):
        self.logger.debug(data)
        if self.verbose:
            print data

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
    fp = open('/tmp/everpad-provider-%s.lock' % getpass.getuser(), 'w')
    try:
        fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # ref: http://dbus.freedesktop.org/doc/dbus-python/api/
        # set_as_default is given and is true, set the new main 
        # loop as the default for all new Connection or Bus instances
        # allows this script to receive DBus calls
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        
        # SlavikZ - Fix occasional everpad-provider segfaults 050814
        dbus.mainloop.glib.threads_init()
        
        app = ProviderApp(args.verbose, sys.argv)
        app.exec_()
    
    except IOError:
        print("geverpad-provider already running")
    except Exception as e:
        app.logger.debug(e)

# allows running daemon.py directly
if __name__ == '__main__':
    main()
