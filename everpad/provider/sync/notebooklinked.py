from sqlalchemy.orm.exc import NoResultFound
from evernote.edam.error.ttypes import EDAMUserException, EDAMSystemException, EDAMErrorCode
from evernote.edam.limits import constants as limits
from evernote.edam.type import ttypes
from evernote.edam.notestore.ttypes import SyncChunk, getLinkedNotebookSyncChunk
from ... import const
from ..exceptions import TTypeValidationFailed
from .. import models
from .base import BaseSync, SyncStatus


class PullLBN(BaseSync):
    """Pull tags from server"""

    # Args:
    #    self.auth_token, self.session,
    #    self.note_store, self.user_store
    #
    def __init__(self, *args, **kwargs):
        super(PullLBN, self).__init__(*args, **kwargs)
        self._exists = []

    def pull(self, chunk_start_after, chunk_end):
        """Pull tags from server"""

        # okay, so _get_all_tags uses a generator to yield each note
        # _get_all_tags using getFilteredSyncChunk returns SyncChunk
        for lbn_meta_ttype in self._get_all_lbn(chunk_start_after, chunk_end):

            # EEE Rate limit from _get_all_notes then break
            if SyncStatus.rate_limit:
                break
            
            self.app.log(
                'Pulling lbn "%s" from remote server.' % lbn_meta_ttype.shareName) 

        # @@@@ This file is just a stub


    # ************ Get All Linked Notebooks **************
    #
    #  Uses getFilteredSyncChunk to pull LNB data
    #  from the server and yield each note for processing.
    #  chunk_start_after will be zero for a full sync and will
    #  be the local store high USN for increment sync
    #
    def _get_all_lbn(self, chunk_start_after, chunk_end):
        """Iterate all notes"""

        while True:
            try:
                sync_chunk = self.note_store.getLinkedNotebookSyncChunk(
                    self.auth_token,
                    chunk_start_after,
                    chunk_end,
                    False
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
            for srv_lbn in sync_chunk.linkedNotebooks:
                # no notes in this chunk                
                if not srv_lbn.guid:
                    break
                yield srv_lbn

            # Here chunkHighUSN is the highest USN returned by the current
            # getFilteredSyncChunk call.  If chunkHighUSN == chunk_end then
            # we have received all Note structures on the server so break.
            # If chunkHighUSN != chunk_end then there is more to get so 
            # chunk_start_after set to chunkHighUSN which will retrieve 
            # starting at chunkHighUSN+1 to chunk_end when calling 
            # getFilteredSyncChunk again - got it?
            if sync_chunk.chunkHighUSN == sync_chunk.updateCount:
                break
            else:
                chunk_start_after = sync_chunk.chunkHighUSN


