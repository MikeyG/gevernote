from evernote.edam.error.ttypes import EDAMUserException, EDAMSystemException, EDAMErrorCode
from PySide import QtCore
from datetime import datetime
from ... import const
from ...specific import AppClass
from .. import tools
from . import note, notebook, tag
from .. import models
import time
import traceback
import socket

from .base import SyncStatus

"""
    Rate Limit handling:
    1.  If provider starts in a Rate Limit period, it will be caught 
    	at _init_network. A sleep command will execute and indicator 
    	will display Rate Limit.  There is really nothing else to do
    	but sleep at that point.

    2.  Early in perform( ) check sync_state.rate_limit and if true
        then sleep until clear.

"""

#sync_info = SyncStatus( )

# ********** SyncThread **********
# 
# from daemon.py 
# subclass PySide.QtCore.QThread and reimplement PySide.QtCore.QThread.run()
# http://srinikom.github.io/pyside-docs/PySide/QtCore/QThread.html
class SyncThread(QtCore.QThread):
    """Sync notes with evernote thread"""
    
    # signals
    # http://qt-project.org/wiki/Signals_and_Slots_in_PySide
    force_sync_signal = QtCore.Signal()
    sync_state_changed = QtCore.Signal(int)
    data_changed = QtCore.Signal()
    
    def __init__(self, *args, **kwargs):
        """Init default values"""
        
        QtCore.QThread.__init__(self, *args, **kwargs)
        
        # non - KDE
        # from PySide.QtCore import QCoreApplication
        # Class = QCoreApplication
        # http://srinikom.github.io/pyside-docs/PySide/QtCore/QCoreApplication.html
        # QCoreApplication * QCoreApplication::instance () [static]
        # Returns a pointer to the application's QCoreApplication (or QApplication) instance.
        self.app = AppClass.instance()
        
        # setup timer
        self._init_timer()
        
        # setup wait_condition and mutex
        self._init_locks()

    # **************************************************************
    # *                                                            *
    # *  Timer routines called by __init__ during initialization   *
    # *     _init_timer(), _init_locks(), update_timer()           *   
    # *                                                            *
    # **************************************************************
                
    # *** Initialize Timer
    # Initialize timer, connect to sync signal, set delay,
    # and start timer.
    # http://qt-project.org/doc/qt-4.8/qtimer.html
    def _init_timer(self):
        """Init timer"""
        
        # Constructs a timer
        self.timer = QtCore.QTimer()
        
        # This signal is emitted when the timer times out - sync
        self.timer.timeout.connect(self.sync)
        
        # call update_timer to set time and start
        self.update_timer()

    # *** Update Timer
    # Stop the timmer, Set the timer delay to user settings,
    # default value, or nothing if manual. Finally, start the timer.
    def update_timer(self):
        """Update sync timer"""
        
        # stop timer
        self.timer.stop()
        
        # initial value of timer from settings
        delay = int(self.app.settings.value('sync_delay') or 0)
        
        # if no delay has been set in the settings then use
        # the default -  DEFAULT_SYNC_DELAY = 30000 * 60
        # WOW - that is a big default delay
        if not delay:
            delay = const.DEFAULT_SYNC_DELAY

        # if delay is not set to manual - SYNC_MANUAL = -1
        # then start the timer - seconds
        if delay != const.SYNC_MANUAL:
            self.timer.start(delay)

    # *** Initialize Locks
    # PySide
    # self.wait_condition and self.mutex
    def _init_locks(self):
        """Init locks"""
        
        # provides a condition variable for synchronizing threads
        # http://srinikom.github.io/pyside-docs/PySide/QtCore/QWaitCondition.html
        self.wait_condition = QtCore.QWaitCondition()
        
        # class provides access serialization between threads
        # http://srinikom.github.io/pyside-docs/PySide/QtCore/QMutex.html
        self.mutex = QtCore.QMutex()

    # **************************************************************
    # *                                                            *
    # *  Timer routines called by run( ) during start              *
    # *     _init_db(), _init_network(), _init_sync()              *   
    # *                                                            *
    # **************************************************************

    # *** Initialize Database
    # Setup database - tools.py    
    def _init_db(self):
        """Init database"""
        
        self.app.log("Execute _init_db")
        self.session = tools.get_db_session()

    # *** Initialize Sync
    # Setup Sync table with values and set status
    def _init_sync(self):
        """Init sync"""
        
        self.app.log("Execute _init_sync")
        
        # set status to None         
        self.status = const.STATUS_NONE
        
        # get current datetime
        # https://docs.python.org/2/library/datetime.html#datetime-objects
        # consider time zone?         
        self.last_sync = datetime.now()
        
        # query Sync table - Return the first result of this Query or None 
        # if the result doesn’t contain any row.
        self.sync_state = self.session.query(models.Sync).first()

        # if the query did not return a result, setup the sync table
        # with update_count 0 and last_sync as current date/time
        # MKG: added Rate Limit defaults
        
        # MKG 041914 - okay, I need to go back and get a couple things straight.
        # I want to set the state of the database at this point. This check was from the 
        # original code, so if the query returns sync_state as false then I am going
        # to say this is an initial sync.  Let's set up sync with current server values 
        # right from the start.
        
        if not self.sync_state:
            # initial "no database" setup
            self.sync_state = models.Sync(
                update_count=0, 
                last_sync=0,
            )
            # update Sync table
            self.session.add(self.sync_state)
            self.session.commit()
        
        # set the rate limit indication to 0
        SyncStatus.rate_limit = 0
        
    # Initialize Network
    # Get get_auth_token get_note_store get_user_store - tools.py
    def _init_network(self):
        """Init connection to remote server"""
        
        self.app.log("Execute _init_network")        
        
        while True:
            try:
                self.auth_token = tools.get_auth_token()
                self.note_store = tools.get_note_store(self.auth_token)
                self.user_store = tools.get_user_store(self.auth_token)
                break
            except EDAMSystemException, e:
                if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                    self.app.log(
                        "Rate limit _init_network: %d minutes - sleeping" % 
                        (e.rateLimitDuration/60)
                    )
                    self.status = const.STATUS_RATE
                    # nothing I can think of doing other than sleeping here
                    # until the rate limit clears
                    time.sleep(e.rateLimitDuration)
                    self.status = const.STATUS_NONE
            except socket.error, e:
                self.app.log(
                    "Couldn't connect to remote server. Got: %s" %
                        traceback.format_exc()
                )
                self.sync_state.connect_error_count+=1
                self.app.log(
                    "Total connect errors: %d" % self.sync_state.connect_error_count)
                time.sleep(30)
                
    # ***** reimplement PySide.QtCore.QThread.run() *****
    #
    # This is the main loop of the thread
    def run(self):
        """Run thread"""
        
        # Fixed an issue here.  If provider is started while the server
        # is not responding due to a Rate Limit then _init_network errors
        # and causes a hang.  I can handle this but I want _init_sync
        # complete before _init_network, so I swapped the execution order to
        # _init_db, _init_sync, _init_network
        # 
        self._init_db()         # setup database
        self._init_sync()       # setup Sync table times
        self._init_network()    # get evernote info

        # Note: Deprecated since version 2.6: mutex module removed in Python 3.
        while True:
            self.mutex.lock()
            self.wait_condition.wait(self.mutex)
            # do sync ....
            self.perform()
            self.mutex.unlock()
            
            # sleep 1 second
            time.sleep(1)  # prevent cpu eating
            
    # ********** end main running loop **************

    # ******** Perform Sync Operations Local and Remote *********
    #
    def perform(self):
        """Perform all sync"""
        
        self.app.log("Execute perform( )")

        # A good place to check and wait if rate limited
        if SyncStatus.rate_limit:
            self.app.log("RateLimit early perform( ) - sleeping")
            self.status = const.STATUS_RATE
            time.sleep(SyncStatus.rate_limit)
            
            # clear rate limit
            SyncStatus.rate_limit = 0

        # set status to sync
        self.status = const.STATUS_SYNC
        
        # Tell the world we are start sync
        self.sync_state_changed.emit(const.SYNC_STATE_START)

        # update server sync info
        self._get_sync_state()
        
        # temp:  --- just setting to true for testing MKG 042814
        force_sync = 1
        
        if force_sync:
            self.app.log("force_sync sync")
            self.sync_state.update_count = 0
            need_to_update = True            
        elif self.sync_state.srv_fullSyncBefore < self.sync_state.srv_current_time:
            # Full sync needed
            self.app.log("fullSyncBefore sync")
            self.sync_state.update_count = 0
            need_to_update = True
        elif self.sync_state.update_count < self.sync_state.srv_update_count:
            # Do incremental sync
            self.app.log("increment sync")
            need_to_update = True
        else:
            self.app.log("local only sync")
            need_to_update = False
 
        # USN stats to log
        self.app.log("Local account updates count:  %s" % self.sync_state.update_count)
        self.app.log("Remote account updates count: %s" % self.sync_state.srv_update_count)        

        try:
            # Need a sync or update?            
            if need_to_update:
                self.remote_changes()
                
            # If not rate limit then do local changes            
            if not SyncStatus.rate_limit: 
                self.local_changes()
            
            # if we get a good finish - update the count to match server
            self.sync_state.update_count = self.sync_state.srv_update_count
            
            self.sync_state.last_sync = datetime.now( )
            self.data_changed.emit()

            if not SyncStatus.rate_limit:             
                self.app.log("Sync performed.")
            else:
                self.app.log("Rate limit no full sync.") 
                           
            # Well sync should be done - I hope
            
        except EDAMSystemException, e:
            if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                self.app.log(
                    "Rate limit end of perform: %d minutes - sleeping" % 
                    (e.rateLimitDuration/60)
                )
                self.session.rollback()
                self._init_db()
                self.status = const.STATUS_RATE
                SyncStatus.rate_limit = e.rateLimitDuration
                self.status = const.STATUS_NONE            
        
        except Exception, e:  # maybe log this
            self.session.rollback()
            self._init_db()
            self.app.log(e)
        
        finally:
            self.sync_state_changed.emit(const.SYNC_STATE_FINISH)
            self.status = const.STATUS_NONE
            self.all_notes = None

        # check - should not set if error
        # self.sync_state.last_sync = datetime.now( )

        # self.data_changed.emit()
        # self.app.log("Sync performed.")

    # *** Get Server Sync State
    # Sync table with current sync status
    def _get_sync_state(self):
        """Get sync state"""
        
        self.app.log("Execute _get_sync_state")
        
        while True:
            try:
                init_sync_state = self.note_store.getSyncState(self.auth_token)
                self.sync_state.srv_current_time = init_sync_state.currentTime 
                self.sync_state.srv_fullSyncBefore = init_sync_state.fullSyncBefore 
                self.sync_state.srv_update_count = init_sync_state.updateCount 
                self.sync_state.srv_uploaded_bytes = init_sync_state.uploaded
                self.session.commit()
                break
            except EDAMSystemException, e:
                if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                    self.app.log(
                        "Rate limit _init_sync_state: %d minutes - sleeping" % 
                        (e.rateLimitDuration/60)
                    )
                    self.status = const.STATUS_RATE
                    # nothing I can think of doing other than sleeping here
                    # until the rate limit clears
                    time.sleep(e.rateLimitDuration)
                    self.status = const.STATUS_NONE        
            except socket.error, e:
                # MKG: I want to track connect errors
                self.sync_state.connect_error_count+=1
                self.app.log(
                    "Couldn't connect to remote server. Got: %s" %
                    traceback.format_exc())
                self.app.log(
                    "Total connect errors: %d" % self.sync_state.connect_error_count)
                # This is most likely a network failure. Return False so
                # everpad-provider won't lock up and can try to sync up in the
                # next run.        

    # *** Force Sync ***
    def force_sync(self):
        """Start sync"""
        self.timer.stop()
        self.sync()
        self.update_timer()

    @QtCore.Slot()
    def sync(self):
        """Do sync"""
        self.wait_condition.wakeAll()

    # ******** Process Remote Changes *********
    # Get all changes from server (evernote) 
    def remote_changes(self):
        """Receive remote changes from evernote"""

        self.app.log('Running remote_changes()')
        
        # Notebooks
        self.sync_state_changed.emit(const.SYNC_STATE_NOTEBOOKS_REMOTE)
        notebook.PullNotebook(*self._get_sync_args()).pull()
        
        # Tags
        self.sync_state_changed.emit(const.SYNC_STATE_TAGS_REMOTE)
        tag.PullTag(*self._get_sync_args()).pull()

        # Notes and Resources
        self.sync_state_changed.emit(const.SYNC_STATE_NOTES_REMOTE)
        note.PullNote(*self._get_sync_args()).pull()

    # ******** Process Local Changes *********
    # Send all changes to server (evernote) 
    def local_changes(self):
        """Send local changes to evernote server"""

        self.app.log('Running local_changes()')

        # Notebooks
        self.sync_state_changed.emit(const.SYNC_STATE_NOTEBOOKS_LOCAL)
        notebook.PushNotebook(*self._get_sync_args()).push()

        # Tags
        self.sync_state_changed.emit(const.SYNC_STATE_TAGS_LOCAL)
        tag.PushTag(*self._get_sync_args()).push()

        # Notes and Resources
        self.sync_state_changed.emit(const.SYNC_STATE_NOTES_LOCAL)
        note.PushNote(*self._get_sync_args()).push()
        
    # ******** Sync Args *********
    # get sync args for local_changes and remote_changes
    def _get_sync_args(self):
        """Get sync arguments"""
        return self.auth_token, self.session, self.note_store, self.user_store


