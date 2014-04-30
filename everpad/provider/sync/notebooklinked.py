














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
                sync_chunk = self.note_store.getFilteredSyncChunk(
                    self.auth_token,
                    chunk_start_after,
                    chunk_end,
                    SyncChunkFilter(
                        includeTags=True,
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
            for srv_tag in sync_chunk.tags:
                # no notes in this chunk                
                if not srv_tag.guid:
                    break
                yield srv_tag

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


