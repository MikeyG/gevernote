from sqlalchemy.orm.exc import NoResultFound
from evernote.edam.error.ttypes import EDAMUserException, EDAMSystemException, EDAMErrorCode
from evernote.edam.limits import constants as limits
from evernote.edam.type import ttypes
from evernote.edam.notestore.ttypes import SyncChunk, SyncChunkFilter
from ... import const
from ..exceptions import TTypeValidationFailed
from .. import models
from .base import BaseSync, SyncStatus
import regex

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
            self.app.log(
                'Pushing notebook "%s" to remote server.' % notebook.name)

            try:
                notebook_ttype = self._create_ttype(notebook)
            except TTypeValidationFailed:
                self.app.log('notebook %s skipped' % notebook.name)
                notebook.action = const.ACTION_NONE
                continue

            if notebook.action == const.ACTION_CREATE:
                self._push_new_notebook(notebook, notebook_ttype)
            elif notebook.action == const.ACTION_CHANGE:
                self._push_changed_notebook(notebook, notebook_ttype)

        self.session.commit()
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
        self.session.commit()


# *************************************************
# **************    Pull Notebook    **************
# *************************************************
class PullNotebook(BaseSync):
    """Pull notebook from server"""

    def __init__(self, *args, **kwargs):
        super(PullNotebook, self).__init__(*args, **kwargs)
        self._exists = []

    def pull(self, chunk_start_after, chunk_end):
        """Receive notebooks from server"""

        # okay, so _get_all_tags uses a generator to yield each note
        # _get_all_tags using getFilteredSyncChunk returns SyncChunk
        for notebook_meta_ttype in self._get_all_notebooks(chunk_start_after, chunk_end):

            # EEE Rate limit from _get_all_notes then break
            if SyncStatus.rate_limit:
                break

            self.app.log(
                'Pulling notebook "%s" from remote server.' % notebook_meta_ttype.name)                
                
            try:
                # check if notebook exists and if needs update
                # also handle conflicts
                notebook = self._update_notebook(notebook_meta_ttype)
                
                # EEE Rate limit from _update_tag then break
                if SyncStatus.rate_limit:
                    break
                # If we get here the note has been created
                
            except NoResultFound:
                
                # the tag is not in the local database so create
                notebook = self._create_notebook(notebook_meta_ttype)

                # EEE Rate limit from _create_notebook then break
                if SyncStatus.rate_limit:
                    break
                # If we get here the note has been created
         
            self._exists.append(notebook.id)

        # commit local changes
        self.session.commit()
        
        # remove unneeded from database
        self._remove_notebooks()

    # **************** Get All Notebooks ****************
    #
    #  Uses getFilteredSyncChunk to pull notebook data
    #  from the server and yield each note for processing.
    #  chunk_start_after will be zero for a full sync and will
    #  be the local store high USN for increment sync
    #
    def _get_all_notebooks(self, chunk_start_after, chunk_end):
        """Iterate all notebooks"""
        
        while True:
            try:
                sync_chunk = self.note_store.getFilteredSyncChunk(
                    self.auth_token,
                    chunk_start_after,
                    chunk_end,
                    SyncChunkFilter(
                        includeNotebooks=True,
                    )
                ) 
            # EEE if a rate limit happens 
            except EDAMSystemException, e:
                if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                    self.app.log(
                        "Rate limit in _get_all_notebooks: %d minutes" % 
                            (e.rateLimitDuration/60)
                    )
                    SyncStatus.rate_limit = e.rateLimitDuration
                    break
        
            # https://www.jeffknupp.com/blog/2013/04/07/
            #       improve-your-python-yield-and-generators-explained/
            # https://wiki.python.org/moin/Generators
            # Each SyncChunk.tags is yielded (yield note) for 
            # create or update 
            try:            
                for srv_notebooks in sync_chunk.notebooks:
                    # no notes in this chunk                
                    if not srv_notebooks.guid:
                        break
                    yield srv_notebooks
            except:
            	if sync_chunk.chunkHighUSN == sync_chunk.updateCount:
            	    break 

            # Here chunkHighUSN is the highest USN returned by the current
            # getFilteredSyncChunk call.  If chunkHighUSN == chunk_end then
            # we have received all Note structures on the server so break.
            # If chunkHighUSN != chunk_end then there is more to get so 
            # chunk_start_after set to chunkHighUSN which will retrieve 
            # starting at chunkHighUSN+1 to chunk_end when calling 
            # getFilteredSyncChunk again - got it?
            chunk_start_after = sync_chunk.chunkHighUSN

    # ************** Update Notebook **************
    #
    def _update_notebook(self, notebook_ttype):
        """Try to update notebook from ttype"""
        
        # is the notebook in the local database?
        # if NoResultFound then return and create new
        notebook = self.session.query(models.Notebook).filter(
            models.Notebook.guid == notebook_ttype.guid,
        ).one()
        
        # if is in database then update it from the server
        if notebook.service_updated < notebook_ttype.serviceUpdated:
            notebook.from_api(notebook_ttype)
            
        # done    
        return notebook


    # ************** Create Notebook **************
    #
    def _create_notebook(self, notebook_ttype):
        """Create notebook from ttype"""
        
        # create new notebook -- Notebook - models.py
        notebook = models.Notebook(guid=notebook_ttype.guid)
        # fill in values 
        notebook.from_api(notebook_ttype)
        
        # add/commit to local database
        self.session.add(notebook)
        self.session.commit()
        
        # done
        return notebook


    # ************** Remove Notebook **************
    #
    def _remove_notebooks(self):
        """Remove not received notebooks"""
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
            
