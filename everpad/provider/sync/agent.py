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

"""
    Rate Limit handling:
        If provider starts in a Rate Limit period, it will be caught 
    	at _init_network. A sleep command will execute and indicator 
    	will display Rate Limit.  There is really nothing else to do
    	but sleep at that point.
    	
    	
    	
    	 
"""


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

   # *** End Initialize Timer

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

   # *** End Initialize Locks

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
        # then start the timer
        if delay != const.SYNC_MANUAL:
            self.timer.start(delay)
            
   # *** End Update Timer

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
        self.session = tools.get_db_session()

    # Initialize Network
    # Get get_auth_token get_note_store get_user_store - tools.py
    def _init_network(self):
        """Init connection to remote server"""
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
            except socket.error:
                time.sleep(30)
                
    # *** Initialize Sync
    # Setup Sync table with values and set status
    def _init_sync(self):
        """Init sync"""
        
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
        
        # My thoughts right now
        # update_count - 0 since no sync
        # last_sync - ??? don't know about this
        # virgin_db - if == 1 then this is a first run
        # rate_limit - if provider is in a rate limit then == 1
        # rate_limit_time - rate limit time - need more here
        # connect_error_count - just play, might want to track later
        
        if not self.sync_state:
            self.sync_state = models.Sync(
                update_count=0, 
                last_sync=self.last_sync,
                virgin_db=1,
                rate_limit=0,
                rate_limit_time=0,
                connect_error_count=0)
            # update Sync table
            self.session.add(self.sync_state)
            self.session.commit()
        else:
            # MKG: zero my play values
            self.sync_state.rate_limit=0
            self.sync_state.rate_limit_time=0
            self.sync_state.connect_error_count=0
            self.session.commit()

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
        
        
        # @@@@ I want to do some other start up checks/calls here
        # ie ... is this a virgin/do I need a full sync (offset) ...
        

        # Deprecated since version 2.6: 
        # The mutex module has been removed in Python 3.
        while True:
            self.mutex.lock()
            self.wait_condition.wait(self.mutex)
            
            # do sync ....
            self.perform()
            
            self.mutex.unlock()
            
            # sleep 1 second
            time.sleep(1)  # prevent cpu eating
    # ********** end main running loop **************

    # ********** Working Routines **********

    # ******** Perform Sync Operations Local and Remote *********
    #
    def perform(self):
        """Perform all sync"""
        self.app.log("Performing sync perform( )")
        
        # set status to sync
        
        # if Rate 
        self.status = const.STATUS_SYNC
        
        #@@@@ don't set until complete !!!  last_sync
        
        # get date/time to set new late sync value
        self.last_sync = datetime.now()

        """        
        I don't want to do this yet MKG
        if self.sync_state.rate_limit:
            if self.sync_state.last_sync < self.sync_state.rate_limit_time:
                self.status = const.STATUS_NONE
                self.app.log("Stopped - Still Rate Limit.")
                return
            else:
                self.sync_state.rate_limit = 0
                self.app.log("Rate Limit cleared.")
        """

        # ??? Tell the world we are start sync
        
        self.sync_state_changed.emit(const.SYNC_STATE_START)

        need_to_update = self._need_to_update()
        
        # we hit a rate limit, might as well bug out here
        if self.sync_state.rate_limit and not need_to_update:
            self.sync_state_changed.emit(const.SYNC_STATE_FINISH)
            self.status = const.STATUS_NONE
            self.data_changed.emit()
            self.app.log("Stopped - Rate Limit.")
            return

        try:
            if need_to_update:
                self.remote_changes()
            self.local_changes()
            
            # if we get a good finish - update the count to match server
            self.sync_state.update_count = self.sync_state.srv_update_count
            
        except Exception, e:  # maybe log this
            self.app.log("perform error")
            self.session.rollback()
            self._init_db()
            self.app.log(e)
        
        finally:
            self.sync_state_changed.emit(const.SYNC_STATE_FINISH)
            self.status = const.STATUS_NONE
            self.all_notes = None

        self.data_changed.emit()
        self.app.log("Sync performed.")


    def _need_to_update(self):
        """Check need for update notes"""
        self.app.log('Checking need for update notes.')
        # Try to update_count.
        
        # okay get a RATE_LIMIT_REACHED on an initial sync
        # http://dev.evernote.com/doc/articles/rate_limits.php
        # getSyncState - updateCount -The total number of updates that have been 
        # performed in the service for this account. This is equal to the 
        # highest USN within the account at the point that this SyncChunk was 
        # generated. If updateCount and chunkHighUSN are identical, that means 
        # that this is the last chunk in the account ... there is no more recent information. 
        try:
            self.sync_state.srv_update_count = self.note_store.getSyncState(
                self.auth_token).updateCount
        except EDAMSystemException, e:
            if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                self.app.log(
                    "Rate limit _need_to_update: %d minutes - sleeping" % 
                        (e.rateLimitDuration/60)
                )
                self.status = const.STATUS_RATE
                # nothing I can think of doing other than sleeping here
                # until the rate limit clears and retry
                time.sleep(e.rateLimitDuration)
                self.status = const.STATUS_SYNC
                update_count = self.note_store.getSyncState(
                    self.auth_token).updateCount
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
            return False
            
        # Remember - don't get here if there was a problem
        
        #XXX: matsubara probably innefficient as it does a SQL each time it
        # accesses the update_count attr?
        self.app.log("Local account updates count:  %s" % self.sync_state.update_count)
        self.app.log("Remote account updates count: %s" % self.sync_state.srv_update_count)

        # true if an update and false if no update needed
        reason = self.sync_state.srv_update_count != self.sync_state.update_count
        
        #@@@@ MOVED okay --- work here do I really want to update before I'm done?
        # self.sync_state.update_count = update_count -- temp:
        # self.sync_state.update_count = 1

        return reason

    
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


    # ******** Sync Args *********
    # get sync args for local_changes and remote_changes
    def _get_sync_args(self):
        """Get sync arguments"""
        return self.auth_token, self.session, self.note_store, self.user_store

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
