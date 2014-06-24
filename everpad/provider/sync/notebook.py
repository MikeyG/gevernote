from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
from sqlalchemy import exc
from evernote.edam.error.ttypes import EDAMUserException, EDAMSystemException, EDAMErrorCode
from evernote.edam.limits import constants as limits
from evernote.edam.type import ttypes
from evernote.edam.notestore.ttypes import SyncChunk, SyncChunkFilter
from ... import const
from ..exceptions import TTypeValidationFailed
from .. import models
from .base import BaseSync, SyncStatus
import regex

# python built-in logging 
import logging
logger = logging.getLogger('gevernote-provider')


# ****** Contains:
#        PushNotebook and PullNoteBook

# *************************************************
# **************    Push Notebook    **************
# *************************************************
class PushNotebook(BaseSync):
    """Notebook sync"""

    def push(self):
        """Push notebook changes to server"""
        
        # for each notebook that requires action
        for notebook in self.session.query(models.Notebook).filter(
            models.Notebook.action != const.ACTION_NONE,
        ):
            logger.info(
                'Pushing notebook "%s" to remote server.' % notebook.name)

            try:
                notebook_ttype = self._create_ttype(notebook)
            except TTypeValidationFailed:
                logger.info('notebook %s skipped' % notebook.name)
                notebook.action = const.ACTION_NONE
                continue

            if notebook.action == const.ACTION_CREATE:
                self._push_new_notebook(notebook, notebook_ttype)
            elif notebook.action == const.ACTION_CHANGE:
                self._push_changed_notebook(notebook, notebook_ttype)

        try:
            self.session.commit()
        except exc.SQLAlchemyError:
            logger.error("Commit error")

        self._merge_duplicates()

    def _create_ttype(self, notebook):
        """Create notebook ttype"""
        kwargs = dict(
            name=notebook.name[
                :limits.EDAM_NOTEBOOK_NAME_LEN_MAX
            ].strip().encode('utf8'),
            defaultNotebook=notebook.default,
        )

        if notebook.stack:
            kwargs['stack'] = notebook.stack[
                :limits.EDAM_NOTEBOOK_STACK_LEN_MAX
            ].strip().encode('utf8')

        if not regex.search(limits.EDAM_NOTEBOOK_NAME_REGEX, notebook.name):
            raise TTypeValidationFailed()

        if notebook.guid:
            kwargs['guid'] = notebook.guid

        return ttypes.Notebook(**kwargs)

    def _push_new_notebook(self, notebook, notebook_ttype):
        """Push new notebook to server"""
        try:
            notebook_ttype = self.note_store.createNotebook(
                self.auth_token, notebook_ttype,
            )
            notebook.guid = notebook_ttype.guid
            notebook.action = const.ACTION_NONE
        except EDAMUserException:
            notebook.action = const.ACTION_DUPLICATE
            self.app.log('Duplicate %s' % notebook_ttype.name)

    def _push_changed_notebook(self, notebook, notebook_ttype):
        """Push changed notebook"""
        try:
            notebook_ttype = self.note_store.updateNotebook(
                self.auth_token, notebook_ttype,
            )
            notebook.action = const.ACTION_NONE
        except EDAMUserException:
            notebook.action = const.ACTION_DUPLICATE
            self.app.log('Duplicate %s' % notebook_ttype.name)

    def _merge_duplicates(self):
        """Merge and remove duplicates"""
        for notebook in self.session.query(models.Notebook).filter(
            models.Notebook.action == const.ACTION_DUPLICATE,
        ):
            try:
                original = self.session.query(models.Notebook).filter(
                    (models.Notebook.action != const.ACTION_DUPLICATE)
                    & (models.Notebook.name == notebook.name)
                ).one()
            except NoResultFound:
                original = self.session.query(models.Notebook).filter(
                    models.Notebook.default == True,
                ).one()

            for note in self.session.query(models.Note).filter(
                models.Note.notebook_id == notebook.id,
            ):
                note.notebook = original

            self.session.delete(notebook)

        try:
            self.session.commit()
        except exc.SQLAlchemyError:
            logger.error("Commit error")
            
# *************************************************
# **************    Pull Notebook    **************
# *************************************************
class PullNotebook(BaseSync):
    """Pull notebook from server"""

    # BaseSync Args:
    #    self.auth_token, self.session,
    #    self.note_store, self.user_store
    #
    def __init__(self, *args, **kwargs):
        super(PullNotebook, self).__init__(*args, **kwargs)
        self._exists = []

    def pull(self, chunk_start_after, chunk_end):
        """Receive notebooks from server"""
        
        # if chunk_start_after is 0 this is a full sync
        # I just want a true or false here i.e. 0 or >0
        self.sync_type = chunk_start_after

        # _get_all_notebooks uses a generator to yield each note
        # _get_all_notebooks using getFilteredSyncChunk returns SyncChunk
        for notebook_meta_ttype in self._get_all_notebooks(chunk_start_after, chunk_end):

            # EEE Rate limit from _get_all_notebooks then break
            if SyncStatus.rate_limit:
                break

            logger.info(
                'Pulling notebook "%s" from remote server.' % notebook_meta_ttype.name)                
                
            # check if notebook exists and if needs update
            # tbd - also handle conflicts
            notebook = self._update_notebook(notebook_meta_ttype)
            
            if not notebook:
                # the tag is not in the local database so create
                notebook = self._create_notebook(notebook_meta_ttype)
                
            # EEE Rate limit from _update or _create then break
            if SyncStatus.rate_limit:
            	 # rollback?
                break
         
            self._exists.append(notebook.id)

        # commit local changes
        try:
            self.session.commit()
        except exc.SQLAlchemyError:
            logger.error("Commit error")
 
        # remove unneeded from database on a full sync
        # handle it differently on incremental - see agent.py
        if not self.sync_type:
            self._remove_notebooks( )

    # **************** Get All Notebooks ****************
    #
    #  Uses getFilteredSyncChunk to pull notebook data
    #  from the server and yield each notebook for processing.
    #  chunk_start_after will be zero for a full sync and will
    #  be the local store high USN for increment sync
    #
    def _get_all_notebooks(self, chunk_start_after, chunk_end):
        """Iterate all notebooks"""
        
        while True:
            try:
                logger.debug("Get Chunk chunk_start_after = %d" % chunk_start_after)
                logger.debug("Get Chunk chunk_end         = %d" % chunk_end)

                if chunk_start_after != chunk_end:
                    sync_chunk = self.note_store.getFilteredSyncChunk(
                        self.auth_token,
                        chunk_start_after,
                        chunk_end,
                        SyncChunkFilter(
                            includeNotebooks=True,
                        )
                    )
                else:
                    # nothing to do so return                    
                    logger.debug("All done before getFilteredSyncChunk call.")
                    break                	
                	 
            # EEE if a rate limit happens 
            except EDAMSystemException, e:
                if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                    logger.error(
                        "Rate limit in _get_all_notebooks: %d minutes" % 
                            (e.rateLimitDuration/60)
                    )

                    # tmp using this to track Rate Limit                    
                    SyncStatus.rate_limit = e.rateLimitDuration
                    break
        
            # https://www.jeffknupp.com/blog/2013/04/07/
            #       improve-your-python-yield-and-generators-explained/
            # https://wiki.python.org/moin/Generators
            # Each SyncChunk.tags is yielded for create or update 
            try:            
                for srv_notebooks in sync_chunk.notebooks:
                    
                    # no notebooks in this chunk?                
                    if not srv_notebooks.guid:
                        logger.debug("No more required guid type in chunk")
                        break
                    
                    # if notebook exists process it                      
                    yield srv_notebooks
            
            except:
                if sync_chunk.chunkHighUSN == sync_chunk.updateCount:
                    logger.debug("All done.")
                    break 

            # Here chunkHighUSN is the highest USN returned by the current
            # getFilteredSyncChunk call.  If chunkHighUSN == chunk_end then
            # we have received all Note structures on the server so break.
            # If chunkHighUSN != chunk_end then there is more to get so 
            # chunk_start_after set to chunkHighUSN which will retrieve 
            # starting at chunkHighUSN+1 to chunk_end when calling 
            # getFilteredSyncChunk again - got it?
            logger.debug("Loop chunk_start_after = %d" % chunk_start_after)
            logger.debug("Loop sync_chunk.chunkHighUSN = %d" % sync_chunk.chunkHighUSN)            
            chunk_start_after = sync_chunk.chunkHighUSN

    # ************** Update Notebook **************
    #
    def _update_notebook(self, notebook_ttype):
        """Try to update notebook from ttype"""
       
        # is the notebook in the local database?
        # if NoResultFound then return and create new
        try:        
            notebook = self.session.query(models.Notebook).filter(
                models.Notebook.guid == notebook_ttype.guid,
            ).one()
            
            logger.debug("Notebook: found notebook.")

            # if is in database then update it from the server
            if notebook.service_updated < notebook_ttype.serviceUpdated:
                logger.debug("Notebook: Updating notebook.")                
                notebook.from_api(notebook_ttype)

        except NoResultFound:
            notebook = False
        except MultipleResultsFound:
            logger.debug("Notebook: MultipleResultsFound checking for update.")            
            
        return notebook

    # ************** Create Notebook **************
    #
    def _create_notebook(self, notebook_ttype):
        """Create notebook from ttype"""

        logger.debug("Notebook: Create notebook.")
                
        # create new notebook -- Notebook - models.py
        notebook = models.Notebook(guid=notebook_ttype.guid)
        # fill in values 
        notebook.from_api(notebook_ttype)
        
        logger.debug("Notebook: Created notebook.")        
        
        # add/commit to local database
        self.session.add(notebook)

        try:
            self.session.commit()
        except exc.SQLAlchemyError:
            logger.error("Commit error")

        return notebook

    # ************** Remove Notebook **************
    #
    def _remove_notebooks(self):
        """Remove not received notebooks"""

        logger.debug("Notebook: Removing notebooks.")
        
        if self._exists:
            q = (~models.Notebook.id.in_(self._exists)
                & (models.Notebook.action != const.ACTION_CREATE)
                & (models.Notebook.action != const.ACTION_CHANGE))
        else:
            q = ((models.Notebook.action != const.ACTION_CREATE)
                & (models.Notebook.action != const.ACTION_CHANGE))

        self.session.query(models.Notebook).filter(
            q).delete(synchronize_session='fetch')
            
   
   # !!!!!!!!!!!!  share notebooks ?????? 
            
