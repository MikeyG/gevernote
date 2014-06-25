from PySide import QtCore
from datetime import datetime
from ... import const
from ...specific import AppClass
from .. import tools
from . import note, notebook, tag, notebooklinked, savedsearch
from .. import models
import time
import traceback
import socket

from evernote.edam.error.ttypes import EDAMUserException, EDAMSystemException, EDAMErrorCode
from evernote.api.client import EvernoteClient

# Keep track of various sync errors, etc.
from .base import SyncStatus
from everpad.provider.enauth import get_auth_token

# python built-in logging 
import logging
logger = logging.getLogger('gevernote-provider')

"""
    Rate Limit handling:
    1.  If provider starts in a Rate Limit period, it will be caught 
    	at _init_network. A sleep command will execute and indicator 
    	will display Rate Limit.  There is really nothing else to do
    	but sleep at that point.

    2.  Early in perform( ) check sync_state.rate_limit and if true
        then sleep until clear.

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
    
    logger = logging.getLogger('gevernote-provider')
    
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
        
        logger.debug("Execute _init_db")
        self.session = tools.get_db_session()

    # *** Initialize Sync
    # Setup Sync table with values and set status
    def _init_sync(self):
        """Init sync"""
        
        logger.debug("Execute _init_sync")
        
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
        
        # flag to indicate full sync needed
        self.sync_state.need_full_sync = 0
        
    # Initialize Network
    # Get get_auth_token get_note_store get_user_store - tools.py
    def _init_network(self):
        """Init connection to remote server"""
        
        logger.debug("Execute _init_network")        
        
        while True:
            try:
            	 # pull token from keyring
                logger.debug("init network auth_token")
                self.auth_token = get_auth_token( )
                                             
                # use EvernoteClient() to get userstore and notestore
                client = EvernoteClient(token=self.auth_token, sandbox=False)
                self.user_store = client.get_user_store()
                self.note_store = client.get_note_store()
                
                # self.note_store = tools.get_note_store(self.auth_token)
                # self.user_store = tools.get_user_store(self.auth_token)

                break
            except EDAMSystemException, e:
                if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                    logger.error(
                        "Rate limit _init_network: %d minutes - sleeping" % 
                        (e.rateLimitDuration/60)
                    )
                    self.status = const.STATUS_RATE
                    # nothing I can think of doing other than sleeping here
                    # until the rate limit clears
                    time.sleep(e.rateLimitDuration)
                    self.status = const.STATUS_NONE
            except socket.error, e:
                logger.error(
                    "Couldn't connect to remote server. Got: %s" %
                        traceback.format_exc()
                )
                SyncStatus.connect_error_count+=1
                logger.error(
                    "Total connect errors: %d" % SyncStatus.connect_error_count)
                time.sleep(30)
                
    # ***** reimplement PySide.QtCore.QThread.run() *****
    #
    # This is the main loop of the thread
    def run(self):
        """Run thread"""
        
        #self.logger.debug('SyncThread starting.')        
        
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
            
            if get_auth_token():
                # do sync ....
                self.perform()
            else:
                logger.error("I shouldn't even be here!")

            self.mutex.unlock()
            
            # sleep 1 second
            time.sleep(1)  # prevent cpu eating
            
    # ********** end main running loop **************

    # ******** Perform Sync Operations Local and Remote *********
    #
    def perform(self):
        """Perform all sync"""
        
        logger.debug("Execute perform( )")

        # A good place to check and wait if rate limited
        if SyncStatus.rate_limit:
            logger.error("RateLimit early perform( ) - sleeping")
            self.status = const.STATUS_RATE
            time.sleep(SyncStatus.rate_limit)
            # clear rate limit
            SyncStatus.rate_limit = 0

        # set status to sync
        self.status = const.STATUS_SYNC
        
        # Tell the world we are start sync
        self.sync_state_changed.emit(const.SYNC_STATE_START)

        # update server sync info
        self._get_sync_state( )

        # USN stats to log. Additionally, update_count will be the start and srv_update_count
        # will be the initial high USN for the getFilteredSyncChunk calls to retrieve data
        # from the server
        logger.info("Agent: Local account updates count:  %s" % self.sync_state.update_count)
        logger.info("Agent: Remote account updates count: %s" % self.sync_state.srv_update_count)  

        if self.sync_state.need_full_sync:
            logger.info("Agent: Sync: force_sync sync")
            # set update_count to 0 for full sync
            self.sync_state.update_count = 0
            need_to_update = True            
        elif self.sync_state.srv_fullSyncBefore > self.sync_state.srv_current_time:
            # Full sync needed
            logger.info("Agent: Sync: fullSyncBefore sync")
            # set update_count to 0 for full sync
            self.sync_state.update_count = 0
            need_to_update = True
        elif self.sync_state.update_count < self.sync_state.srv_update_count:
            # Do incremental sync
            logger.info("Agent: Sync: increment sync")
            need_to_update = True
        else:
            logger.info("Agent: Sync: local only sync")
            need_to_update = False
 
        # Need a sync or update?            
        if need_to_update:
            logger.debug("Agent: Need to update - running remote.")
            self.remote_changes(
                self.sync_state.update_count,
                self.sync_state.srv_update_count
            )
        
        # No fancy stuff, just brute checks        
        if not SyncStatus.rate_limit:
            logger.debug("Agent: running local.")
            # If not rate limit then do local changes            
            self.local_changes( )
            
        # If Rate Limit in either remote or local, tell us
        # cleanup and get out        
        if SyncStatus.rate_limit:
            logger.error("Rate limit no full sync.")
            self.session.rollback()
            self._init_db()
            self.data_changed.emit()
            self.status = const.STATUS_RATE
            self.sync_state_changed.emit(const.SYNC_STATE_FINISH) 
        else:
            logger.info("Sync performed.")	

            # if we get a good finish - update the count to match server
            self.sync_state.update_count = self.sync_state.srv_update_count
            # last sync date/time set to current
            self.sync_state.last_sync = datetime.now( )
            # set need_full_sync false so incremental updates will happen
            self.sync_state.need_full_sync = 0
            # tell everyone we are done
            self.data_changed.emit()
            self.status = const.STATUS_NONE
            self.sync_state_changed.emit(const.SYNC_STATE_FINISH)
            
            logger.debug("Agent: Sync signals complete.")

    # *** Get Server Sync State
    # Sync table with current sync status
    def _get_sync_state(self):
        """Get sync state"""
        
        logger.debug("Execute _get_sync_state")
        
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
                    logger.error(
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
                SyncStatus.connect_error_count+=1
                logger.error(
                    "Couldn't connect to remote server. Got: %s" %
                    traceback.format_exc())
                logger.error(
                    "Total connect errors: %d" % SyncStatus.connect_error_count)
                # This is most likely a network failure. Return False so
                # everpad-provider won't lock up and can try to sync up in the
                # next run.        

    # ****************** Force Sync *********************
    # Handles self.app.provider.sync(  )
    # This is a sync started by and external trigger so 
    # need_full_sync will be true
    #
    def force_sync(self):
        """Start sync"""
        logger.debug("Force sync called")
        self.timer.stop( )
        self.sync_state.need_full_sync = 1
        self.sync( )
        self.update_timer( )
        logger.debug("Force sync complete")

    @QtCore.Slot()
    def sync(self):
        """Do sync"""
        logger.debug("Sync slot - wakeall")
        self.wait_condition.wakeAll()

    # ******** Process Remote Changes *********
    # Get all changes from server (evernote) 
    def remote_changes(self, chunk_start_after, chunk_end):
        """Receive remote changes from evernote"""

        logger.debug('Running remote_changes()')
        
        # Notebooks
        logger.debug("Agent: PullNotebook.")
        self.sync_state_changed.emit(const.SYNC_STATE_NOTEBOOKS_REMOTE)
        notebook.PullNotebook(*self._get_sync_args()).pull(chunk_start_after, chunk_end)
        #if not SyncStatus.rate_limit and chunk_start_after:
        #   notebook.ExpungeNotebook(*self._get_sync_args()).pull(chunk_start_after, chunk_end)
        if SyncStatus.rate_limit:
            return
        	
        # Tags
        logger.debug("Agent: PullTag.")
        self.sync_state_changed.emit(const.SYNC_STATE_TAGS_REMOTE)
        tag.PullTag(*self._get_sync_args()).pull(chunk_start_after, chunk_end)
        if SyncStatus.rate_limit:
            return
            
        # Notes and Resources
        logger.debug("Agent: PullNote.")
        self.sync_state_changed.emit(const.SYNC_STATE_NOTES_REMOTE)
        note.PullNote(*self._get_sync_args()).pull(chunk_start_after, chunk_end)
        if SyncStatus.rate_limit:
            return
            
        # Linked Notebooks
        logger.debug("Agent: PullNoteLBN.")
        self.sync_state_changed.emit(const.SYNC_STATE_LBN_REMOTE)
        notebooklinked.PullLBN(*self._get_sync_args()).pull(chunk_start_after, chunk_end)
        if SyncStatus.rate_limit:
            return
                    
        # Searches
        logger.debug("Agent: PullSearch.")
        self.sync_state_changed.emit(const.SYNC_STATE_SEARCHES_REMOTE)
        savedsearch.PullSearch(*self._get_sync_args()).pull(chunk_start_after, chunk_end)

    # ******** Process Local Changes *********
    # Send all changes to server (evernote) 
    def local_changes(self):
        """Send local changes to evernote server"""

        logger.debug('Running local_changes()')

        # Notebooks
        logger.debug("Agent: PushNotebook.")
        self.sync_state_changed.emit(const.SYNC_STATE_NOTEBOOKS_LOCAL)
        notebook.PushNotebook(*self._get_sync_args()).push()
        if SyncStatus.rate_limit:
            return
            
        # Tags
        logger.debug("Agent: PushTags.")
        self.sync_state_changed.emit(const.SYNC_STATE_TAGS_LOCAL)
        tag.PushTag(*self._get_sync_args()).push()
        if SyncStatus.rate_limit:
            return
            
        # Notes and Resources
        logger.debug("Agent: PushNote.")
        self.sync_state_changed.emit(const.SYNC_STATE_NOTES_LOCAL)
        note.PushNote(*self._get_sync_args()).push()
        
    # ******** Sync Args *********
    # get sync args for local_changes and remote_changes
    def _get_sync_args(self):
        """Get sync arguments"""
        logger.debug('Called Get sync args')        
        return self.auth_token, self.session, self.note_store, self.user_store


