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
#        PushTag and Pulltag


class PushTag(BaseSync):
    """Push tags to server"""

    def push(self):
        """Push tags"""
        for tag in self.session.query(models.Tag).filter(
            models.Tag.action != const.ACTION_NONE,
        ):
            self.app.log('Pushing tag "%s" to remote server.' % tag.name)

            try:
                tag_ttype = self._create_ttype(tag)
            except TTypeValidationFailed:
                tag.action = const.ACTION_NONE
                self.app.log('tag %s skipped' % tag.name)
                continue

            if tag.action == const.ACTION_CREATE:
                self._push_new_tag(tag, tag_ttype)
            elif tag.action == const.ACTION_CHANGE:
                self._push_changed_tag(tag, tag_ttype)

        self.session.commit()

    def _create_ttype(self, tag):
        """Create tag ttype"""
        if not regex.search(limits.EDAM_TAG_NAME_REGEX, tag.name):
            raise TTypeValidationFailed()

        kwargs = dict(
            name=tag.name[:limits.EDAM_TAG_NAME_LEN_MAX].strip().encode('utf8'),
        )

        if tag.guid:
            kwargs['guid'] = tag.guid

        return ttypes.Tag(**kwargs)

    def _push_new_tag(self, tag, tag_ttype):
        """Push new tag"""
        try:
            tag_ttype = self.note_store.createTag(
                self.auth_token, tag_ttype,
            )
            tag.guid = tag_ttype.guid
            tag.action = const.ACTION_NONE
        except EDAMUserException as e:
            self.app.log(e)

    def _push_changed_tag(self, tag, tag_ttype):
        """Push changed tag"""
        try:
            self.note_store.updateTag(
                self.auth_token, tag_ttype,
            )
            tag.action = const.ACTION_NONE
        except EDAMUserException as e:
            self.app.log(e)


class PullTag(BaseSync):
    """Pull tags from server"""

    # Args:
    #    self.auth_token, self.session,
    #    self.note_store, self.user_store
    #
    def __init__(self, *args, **kwargs):
        super(PullTag, self).__init__(*args, **kwargs)
        self._exists = []

    def pull(self, chunk_start_after, chunk_end):
        """Pull tags from server"""

        # okay, so _get_all_tags uses a generator to yield each note
        # _get_all_tags using getFilteredSyncChunk returns SyncChunk
        for tag_meta_ttype in self._get_all_tags(chunk_start_after, chunk_end):

            # EEE Rate limit from _get_all_notes then break
            if SyncStatus.rate_limit:
                break
            
            self.app.log(
                'Pulling tag "%s" from remote server.' % tag_meta_ttype.name) 

            try:
                # check if tag exists and if needs update
                # also handle conflicts
                tag = self._update_tag(tag_meta_ttype)
                
                # EEE Rate limit from _update_tag then break
                if SyncStatus.rate_limit:
                    break
                # If we get here the note has been created

                
            except NoResultFound:
                
                # the tag is not in the local database so create
                tag = self._create_tag(tag_meta_ttype)

                # EEE Rate limit from _create_tag then break
                if SyncStatus.rate_limit:
                    break
                # If we get here the note has been created
                
            self._exists.append(tag.id)

        self.session.commit()
        self._remove_tags()

    # **************** Get All Tags ****************
    #
    #  Uses getFilteredSyncChunk to pull tag data
    #  from the server and yield each note for processing.
    #  chunk_start_after will be zero for a full sync and will
    #  be the local store high USN for increment sync
    #
    def _get_all_tags(self, chunk_start_after, chunk_end):
        """Iterate all tags"""
        
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
            try:            
                for srv_tag in sync_chunk.tags:
                    # no notes in this chunk                
                    if not srv_tag.guid:
                        break
                    yield srv_tag
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

    # new tag
    def _create_tag(self, tag_ttype):
        """Create tag from server"""
        
        try:
            # Is there a conflict?
            tag = self.session.query(models.Tag).filter(
                models.Tag.name == tag_ttype.name.decode('utf8'),
            ).one()
            
            # TBD append a number to conflict tag name and create
            
            self.app.log("Tag conflict")
            
        except NoResultFound:        
            tag = models.Tag(guid=tag_ttype.guid)
            tag.from_api(tag_ttype)
        
        self.session.add(tag)
        self.session.commit()
        
        return tag

    # update tag
    def _update_tag(self, tag_ttype):
        """Update tag if exists"""
        tag = self.session.query(models.Tag).filter(
            models.Tag.guid == tag_ttype.guid,
        ).one()
        if tag.name != tag_ttype.name.decode('utf8'):
            tag.from_api(tag_ttype)
        return tag

    # remove tag
    def _remove_tags(self):
        """Remove not exist tags"""
        if self._exists:
            q = (~models.Tag.id.in_(self._exists)
                & (models.Tag.action != const.ACTION_CREATE))
        else:
            q = (models.Tag.action != const.ACTION_CREATE)
        self.session.query(models.Tag).filter(q).delete(
            synchronize_session='fetch')
