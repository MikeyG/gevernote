from sqlalchemy.orm.exc import NoResultFound
from evernote.edam.error.ttypes import EDAMUserException, EDAMSystemException, EDAMErrorCode
from evernote.edam.limits import constants as limits
from evernote.edam.type import ttypes
from evernote.edam.notestore.ttypes import SyncChunk, SyncChunkFilter
from ... import const
from ..exceptions import TTypeValidationFailed
from .. import models
from .base import BaseSync, SyncStatus


class PullSearch(BaseSync):
    """Pull Search from server"""

    # Args:
    #    self.auth_token, self.session,
    #    self.note_store, self.user_store
    #
    def __init__(self, *args, **kwargs):
        super(PullSearch, self).__init__(*args, **kwargs)
        self._exists = []

    def pull(self, chunk_start_after, chunk_end):
        """Pull tags from server"""
        
        self.app.log("Saved searches")
        
        # okay, so _get_all_lbn uses a generator to yield each record
        for search_meta_ttype in self._get_all_search(chunk_start_after, chunk_end):

            # EEE Rate limit from _get_all_notes then break
            if SyncStatus.rate_limit:
                break
            
            self.app.log(
                'Pulling search "%s" from remote server.' % search_meta_ttype.name)
            
            self.app.log("Saved searches done") 

        # @@@@ This file is just a stub


    # ************ Get All Saved Searches **************
    #
    #  Uses getFilteredSyncChunk to pull LNB data
    #  from the server and yield each note for processing.
    #  chunk_start_after will be zero for a full sync and will
    #  be the local store high USN for increment sync
    #
    def _get_all_search(self, chunk_start_after, chunk_end):
        """Iterate all searches"""

        while True:
            try:
                sync_chunk = self.note_store.getFilteredSyncChunk(
                    self.auth_token,
                    chunk_start_after,
                    chunk_end,
                    SyncChunkFilter(
                        includeSearches=True,
                    )
                ) 

            # EEE if a rate limit happens 
            except EDAMSystemException, e:
                if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                    self.app.log(
                        "Rate limit in _get_all_tags: %d minutes" % 
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
                for srv_search in sync_chunk.searches:
                    # no notes in this chunk                
                    if not srv_search.guid:
                        break
                    yield srv_search
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
            

