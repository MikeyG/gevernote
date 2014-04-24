from BeautifulSoup import BeautifulSoup
from sqlalchemy.orm.exc import NoResultFound
from everpad.tools import sanitize
from evernote.edam.error.ttypes import EDAMUserException, EDAMSystemException, EDAMErrorCode
from evernote.edam.limits import constants as limits
from evernote.edam.type import ttypes
from evernote.edam.notestore.ttypes import NoteFilter, NotesMetadataResultSpec
from ... import const
from .. import models
from .base import BaseSync
import time
import binascii


# ****** Note:  BaseSync - Base class for sync - base.py

# *************************************************
# **************** ShareNoteMixin  ****************
# *************************************************
# Used by PushNote(BaseSync, ShareNoteMixin)
class ShareNoteMixin(object):
    """Mixin with methods for sharing notes"""

    def _get_shard_id(self):
        """Receive shard id, not cached because can change"""
        return self.user_store.getUser(self.auth_token).shardId

    def _share_note(self, note, share_date=None):
        """Share or receive info about sharing"""
        try:
            # @@@@@ API call could get Rate limit            
            share_key = self.note_store.shareNote(self.auth_token, note.guid)
            note.share_url = "https://www.evernote.com/shard/{}/sh/{}/{}".format(
                self._get_shard_id(), note.guid, share_key,
            )
            note.share_date = share_date or int(time.time() * 1000)
            note.share_status = const.SHARE_SHARED
            self.session.commit()
        except EDAMUserException as e:
            note.share_status = const.SHARE_NONE
            self.app.log('Sharing note %s failed' % note.title)
            self.app.log(e)

    def _stop_sharing_note(self, note):
        """Stop sharing note"""
        note.share_status = const.SHARE_NONE
        note.share_date = None
        note.share_url = None
        self.session.commit()


# *************************************************
# ****************    Push Note    ****************
# *************************************************
class PushNote(BaseSync, ShareNoteMixin):
    """Push note to remote server"""

    def push(self):
        """Push note to remote server"""
        
        # for all notes where the action is not None, Noexsist, or Conflict
        for note in self.session.query(models.Note).filter(
            ~models.Note.action.in_((
                const.ACTION_NONE, const.ACTION_NOEXSIST, const.ACTION_CONFLICT,
            ))
        ):

            # Push sequence:
            #  
            # Action = Create, Change, Delete  
            #    |
            #    |- Create - _push_new_note 
            #    |                |
            #    |                |-  _prepare_content
            #    |                |
            #    |                 -  _prepare_resources
            #    |
            #    |- Change - _push_changed_note
            #    |
            #    |- Delete - _delete_note
            #    |
            #    ----------- share_status
            #                     |
            #                     |
            #                     |- NEED_SHARE - _share_note
            #                     |
            #                     |- NEED_STOP - _stop_sharing_note
 
            self.app.log('Pushing note "%s" to remote server.' % note.title)
            
            note_ttype = self._create_ttype(note)
            
            # create note
            if note.action == const.ACTION_CREATE:
                self._push_new_note(note, note_ttype)
            # change note
            elif note.action == const.ACTION_CHANGE:
                self._push_changed_note(note, note_ttype)
            # delete note
            elif note.action == const.ACTION_DELETE:
                self._delete_note(note, note_ttype)

            # handle sharing
            if note.share_status == const.SHARE_NEED_SHARE:
                self._share_note(note)
            elif note.share_status == const.SHARE_NEED_STOP:
                self._stop_sharing_note(note)

        # commit changes to database
        self.session.commit()


    # **************** Create Note ****************
    #
    # note is a database note data structure
    
    def _create_ttype(self, note):
        """Create ttype for note"""
        kwargs = dict(
            title=note.title[:limits.EDAM_NOTE_TITLE_LEN_MAX].strip().encode('utf8'),
            content=self._prepare_content(note.content),
            tagGuids=map(
                lambda tag: tag.guid, note.tags,
            ),
            resources=self._prepare_resources(note),
        )

        if note.notebook:
            kwargs['notebookGuid'] = note.notebook.guid

        if note.guid:
            kwargs['guid'] = note.guid

        return ttypes.Note(**kwargs)

    def _prepare_resources(self, note):
        """Prepare note resources"""
        return map(
            lambda resource: ttypes.Resource(
                noteGuid=note.guid,
                data=ttypes.Data(body=open(resource.file_path).read()),
                mime=resource.mime,
                attributes=ttypes.ResourceAttributes(
                    fileName=resource.file_name.encode('utf8'),
                ),
            ), self.session.query(models.Resource).filter(
                (models.Resource.note_id == note.id)
                & (models.Resource.action != const.ACTION_DELETE)
            ),
        )

    def _prepare_content(self, content):
        """Prepare content"""
        enml_content = (u"""
            <!DOCTYPE en-note SYSTEM "http://xml.evernote.com/pub/enml2.dtd">
            <en-note>{}</en-note>
        """.format(sanitize(
            html=content[:limits.EDAM_NOTE_CONTENT_LEN_MAX]
        ))).strip().encode('utf8')

        soup = BeautifulSoup(enml_content, selfClosingTags=[
            'img', 'en-todo', 'en-media', 'br', 'hr',
        ])

        return str(soup)


    # **************** Push Note ****************
    #
    def _push_new_note(self, note, note_ttype):
        """Push new note to remote"""
        try:
            note_ttype = self.note_store.createNote(self.auth_token, note_ttype)
            note.guid = note_ttype.guid

        except EDAMUserException as e:
            note.action = const.ACTION_NONE
            self.app.log('Push new note "%s" failed.' % note.title)
            self.app.log(e)
        finally:
            note.action = const.ACTION_NONE


    # **************** Create Note ****************
    #
    def _push_changed_note(self, note, note_ttype):
        """Push changed note to remote"""
        try:
            self.note_store.updateNote(self.auth_token, note_ttype)
        except EDAMSystemException, e:
            if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                self.app.log("Rate limit reached: %d seconds" % e.rateLimitDuration)
                self.sync_state.rate_limit = e.rateLimitDuration
                self.sync_state.rate_limit_time = datetime.now() + datetime.timedelta(seconds=e.rateLimitDuration)
        except EDAMUserException as e:
            self.app.log('Push changed note "%s" failed.' % note.title)
            self.app.log(note_ttype)
            self.app.log(note)
            self.app.log(e)
        finally:
            note.action = const.ACTION_NONE


    # **************** Delete Note ****************
    #
    def _delete_note(self, note, note_ttype):
        """Delete note"""
        try:
            self.note_store.deleteNote(self.auth_token, note_ttype.guid)
        except EDAMSystemException, e:
            if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                self.app.log("Rate limit reached: %d seconds" % e.rateLimitDuration)
                self.sync_state.rate_limit = e.rateLimitDuration
                self.sync_state.rate_limit_time = datetime.now() + datetime.timedelta(seconds=e.rateLimitDuration)
        except EDAMUserException as e:
            self.app.log('Note %s already removed' % note.title)
            self.app.log(e)
        finally:
            self.session.delete(note)


# *************************************************
# ****************    Pull Note    ****************
# *************************************************
class PullNote(BaseSync, ShareNoteMixin):
    """Pull notes"""

    def __init__(self, *args, **kwargs):
        super(PullNote, self).__init__(*args, **kwargs)
        self._exists = []

    def pull(self):
        """Pull notes from remote server"""

        # okay, so _get_all_notes uses a generator to yield each note
        # one at a time - great leap for a python dummy such as myself
        # _get_all_notes using findNotesMetadata returns NotesMetadataList
        for note_meta_ttype in self._get_all_notes():
            
            # If no title returns "Untitled note"
            self.app.log(
                'Pulling note "%s" from remote server.' % note_meta_ttype.title)
         
            # note_ttype is a NotesMetadataList -> NoteMetadata (notes)
            # structure of the note
            
            # @@@@ the resource grab/update is going to have to happen in
            #  _create_note  _update_note          
            
            
            # Pull sequence:
            #  
            # _update_note  
            #    |
            #    |- note guid in database? 
            #        | No          | Yes                               
            #        |             |
            #        |             server note         
            #   _create_note       newer
            #        |             |----- Yes --- _get_full_note
            #        |             |                 |
            #   _get_full_note     |              local note
            #                      |         ---- also changed
            #                      |         |            |
            #                      |         | Yes        | No
            #                      return    |            |
            #                                |            |
            #                                |         from_api
            #                          _create_conflict
            #
            
            try:
                note, note_full_ttype = self._update_note(note_meta_ttype)
            except NoResultFound:
                note, note_full_ttype = self._create_note(note_meta_ttype)
                
            self.app.log(note_meta_ttype.guid)
            self.app.log(note_full_ttype.guid)

            
            # At this point note is the note as defind in models.py
            self._exists.append(note.id)
            
            # NotesMetadataList - includeAttributes
            # set or unset sharing
            self._check_sharing_information(note, note_meta_ttype)
            	            
            # Here is where we get the resources
            resource_ids = self._receive_resources(note, note_meta_ttype, note_full_ttype)
            
            if resource_ids:
                 self._remove_resources(note, resource_ids)

        #@@@@ end of for note_meta_ttype in self._get_all_note        
        
        # commit to local database
        self.session.commit()

        # remove unused notes
        self._remove_notes()


    # **************** Get All Notes ****************
    #
    def _get_all_notes(self):
        """Iterate all notes"""
        
        self.app.log("get_all_notes")
        offset = 0

        # Function: NoteStore.findNotes - DEPRECATED. Use findNotesMetadata
        # NotesMetadataList findNotesMetadata(string authenticationToken,
        #                            NoteFilter filter,
        #                            i32 offset,
        #                            i32 maxNotes,
        #                            NotesMetadataResultSpec resultSpec)
        # throws Errors.EDAMUserException, Errors.EDAMSystemException, Errors.
        #        EDAMNotFoundException

        # From 0 (offset) to EDAM_USER_NOTES_MAX - return NotesMetadataList
        #

        while True:
            try:
                note_list = self.note_store.findNotesMetadata(
                    self.auth_token, 
                    NoteFilter(
                        order=ttypes.NoteSortOrder.UPDATED,
                        ascending=False,
                    ), 
                    offset, 
                    limits.EDAM_USER_NOTES_MAX,
                    NotesMetadataResultSpec(
                        includeTitle=True,
                        includeCreated = True,
                        includeUpdated=True,
                        includeDeleted=True,
                        includeAttributes=True,
                        includeLargestResourceSize=True,
                    )
                )
            # if a rate limit happens because of findNotesMetadata
            except EDAMSystemException, e:
                if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                    self.app.log(
                        "Rate limit in _get_all_notes: %d minutes" % 
                            (e.rateLimitDuration/60)
                    )
                    break

            # https://www.jeffknupp.com/blog/2013/04/07/
            #       improve-your-python-yield-and-generators-explained/
            # https://wiki.python.org/moin/Generators
            # Each NotesMetadataList.notes is yielded (yield note) for 
            # create or update in pull()
            for note in note_list.notes:
                yield note

            # inc offset: okay, since we start from offset 0 then startIndex
            # should be 0 on first pass then add len(note_list.notes) which
            # should be total number of notes we grab with the findNotesMetadata
            # call, so offset will be our max notes grabbed this pass
            offset = note_list.startIndex + len(note_list.notes)
            
            # this will be zero if all the notes were received from the 
            # findNotesMetadata (totalNotes)
            # I guess because API:
            # "The service will return a set of notes that is no larger than this number, 
            # but may return fewer notes if needed. The NoteList.totalNotes field in the 
            # return value will indicate whether there are more values available after 
            # the returned set."
            # -- just in case findNotesMetadata does not return all on first call
            if note_list.totalNotes - offset <= 0:
                break
        
        # #################  end while True  ################# 


    # **************** Get Full Note ****************
    #
    # Get the note data from API and return it
    def _get_full_note(self, note_ttype):
        """Get full note"""
        
        # Types.Note getNote(string authenticationToken,
        #           Types.Guid guid,
        #           bool withContent,
        #           bool withResourcesData,
        #           bool withResourcesRecognition,
        #           bool withResourcesAlternateData)
        # NOTE!!! service will include the meta-data for each 
        # resource in the note, but the binary contents of the resources 
        # and their recognition data will be omitted
        try:
            note_full_ttype = self.note_store.getNote(
                self.auth_token, note_ttype.guid,
                True, True, True, True,
            )
        except EDAMSystemException, e:
            if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                self.app.log(
                    "Rate limit _get_full_note: %d minutes" % 
                        (e.rateLimitDuration/60)
                )
                self.sync_state.rate_limit = e.rateLimitDuration        
        
        return note_full_ttype


    # **************** Get Resource Data ****************
    #
    # Get the note data from API and return it
    # MKG: Verified this works 12Apr14
    # -- need some error coding
    # 
    def _get_resource_data(self, resource):
        """Get resource data"""
        
        # string getResourceData(
        #         string authenticationToken,
        #         Types.Guid guid)
        
        try:
            data_body = self.note_store.getResourceData(
                self.auth_token, resource.guid)
        except EDAMSystemException, e:
            if e.errorCode == EDAMErrorCode.RATE_LIMIT_REACHED:
                self.app.log(
                    "Rate limit _get_resource_data: %d minutes" % 
                        (e.rateLimitDuration/60)
                )
                self.sync_state.rate_limit = e.rateLimitDuration
                return

        with open(resource.file_path, 'w') as data:
            data.write(data_body)
            

    # **************** Create Note ****************
    #
    # On entry note_ttype is Note structure that includes all metadata 
    # (attributes, resources, etc.), but will not include the ENML content 
    # of the note or the binary contents of any resources.
    #
    # _create_note pulls ENML content of the note and stores the note data
    # in the database
    def _create_note(self, note_meta_ttype):
        """Create new note"""
        
        # returns Types.Note with Note content, binary contents 
        # of the resources and their recognition data will be omitted
        note_full_ttype = self._get_full_note(note_meta_ttype)

        # !!!!! Rate Limit handle
        
        # So now I understand the continued use of note_ttype
        # if it gets to create then missing info is ADDED to 
        # note_ttype ... less resources binary info

        # Put note into local database
        #    ... create Note ORM with guid
        note = models.Note(guid=note_full_ttype.guid)
        #    ... add other note information
        note.from_api(note_full_ttype, self.session)
        
        # ... commit note data
        self.session.add(note)
        
        self.session.commit()
       
        # Is note the models.py version at this point?
        # why yes it is - confused yet?
        # does return note signal end of yield?
        return note, note_full_ttype
        

    # **************** Update Note****************
    #
    # note_ttype is NotesMetadataList -> NoteMetadata.notes structure 
    # that includes metadata, see _get_all_notes
    def _update_note(self, note_meta_ttype):
        """Update changed note"""
        
        # queries for note guid and returns the note if
        # exists in database - if not exists NoResultFound and return 
        note = self.session.query(models.Note).filter(
            models.Note.guid == note_meta_ttype.guid,
        ).one()

        # --> note guid exists in database, check for update
        #     if not then return and execute the except to 
        #     create a new local note
        
        # - if note in database is older than server then true 
        # - if const.ACTION_CHANGE has also been changed local so
        #   create conflict note  
        # if in database if ! const.ACTION_CHANGE
        if note.updated < note_meta_ttype.updated:
            
            # get full note
            note_full_ttype = self._get_full_note(note_ttype)
            
            # conflict because the server note is newer than
            # the local note in addition the local note has changed            
            if note.action == const.ACTION_CHANGE:
            	 # create conflict note
                self._create_conflict(note, note_full_ttype)
            else:
                # else update database with new sever note
                note.from_api(note_full_ttype, self.session)
        
        else:
            # okay, hope this works.  If no update or conflict then,
            # I believe, the query returned an error, but note_full_ttype
            # needs to be set to None for the return
            note_full_ttype = None
        
        return note, note_full_ttype
    
    
    # **************** Create Conflict ****************
    #
    # This is called when updating and server note is newer
    # local and local has changed since last sync
    #   note = database note
    #   note_ttype = full note structure
    def _create_conflict(self, note, note_full_ttype):
        """Create conflict note"""
        
        # generate a new local note and populate it with
        # server note data
        conflict_note = models.Note()
        conflict_note.from_api(note_full_ttype, self.session)
        
        # set the conflict note guid as empty string
        conflict_note.guid = ''
        # set status as a conflict 
        conflict_note.action = const.ACTION_CONFLICT
        # relate the conflict and local note for reference
        conflict_note.conflict_parent_id = note.id
        
        # commit to database
        self.session.add(conflict_note)
        self.session.commit()

    
    # **************** Remove Note ****************
    def _remove_notes(self):
        """Remove not exists notes"""
        if self._exists:
            q = ((~models.Note.id.in_(self._exists) |
                ~models.Note.conflict_parent_id.in_(self._exists)) &
                ~models.Note.action.in_((
                    const.ACTION_NOEXSIST, const.ACTION_CREATE,
                    const.ACTION_CHANGE, const.ACTION_CONFLICT)))
        else:
            q = (~models.Note.action.in_((
                    const.ACTION_NOEXSIST, const.ACTION_CREATE,
                    const.ACTION_CHANGE, const.ACTION_CONFLICT)))
        self.session.query(models.Note).filter(q).delete(
            synchronize_session='fetch')
        self.session.commit()

    
    # **************** Receive Resource ****************
    #
    # note is the note as defind in models.py
    # note_ttype == Types.Note
    def _receive_resources(self, note, note_meta_ttype, note_full_ttype):
        """Receive note resources"""

        # empty resource id list        
        resources_ids = []
        
        # !!!!! need work here        
        if note_meta_ttype.largestResourceSize or note_full_ttype == None:
            # get full note
            note_full_ttype = self._get_full_note(note_meta_ttype)

        # Update note resources in database and download or delete
        # actual binary data?  See resource.from_api in models.py
        
        # try: looks in database for the resource guid, if
        # not found fall though to except.  If in the database, append to the 
        # list and check hash to verify the existing resource.  If the resource
        # has changed then update database --- !!! I also need to download it again !!!!
        # The except handles resources that do not exist.  
        for resource_ttype in note_full_ttype.resources or []:
            try:
                # Is the resource in the database? If not then except NoResultFound  
                resource = self.session.query(models.Resource).filter(
                    models.Resource.guid == resource_ttype.guid,
                ).one()

                # append resource id to list
                resources_ids.append(resource.id)

                # if resource changed (hash does not match) then
                # re-get resource
                if resource.hash != binascii.b2a_hex(
                    resource_ttype.data.bodyHash,
                ):
                    resource.from_api(resource_ttype)
                    self._get_resource_data(resource)
                    #@@@@ do I need session.commit() here????
                    # I put it here for now
                    self.session.commit()
                    
            # resourse not found in database then:
            except NoResultFound:
                # Make new database entry and get resource
                resource = models.Resource(
                    guid=resource_ttype.guid,
                    note_id=note.id,
                )
                resource.from_api(resource_ttype)
                self._get_resource_data(resource)
                
                self.session.add(resource)
                self.session.commit()
                resources_ids.append(resource.id)

        return resources_ids

    
    # **************** Remove Resource ****************
    #
    def _remove_resources(self, note, resources_ids):
        """Remove non exists resources"""
        self.session.query(models.Resource).filter(
            ~models.Resource.id.in_(resources_ids)
            & (models.Resource.note_id == note.id)
        ).delete(synchronize_session='fetch')
        self.session.commit()

    
    # **************** Check Sharing Info ****************
    #
    # Set (_share_note) or unset (_stop_sharing_note) sharing
    def _check_sharing_information(self, note, note_ttype):
        """Check actual sharing information"""
        if not (
            note_ttype.attributes.shareDate or note.share_status in (
                const.SHARE_NONE, const.SHARE_NEED_SHARE,
            )
        ):
            self._stop_sharing_note(note)
        elif not (
            note_ttype.attributes.shareDate == note.share_date
            or note.share_status in (
                const.SHARE_NEED_SHARE, const.SHARE_NEED_STOP,
            )
        ):
            self._share_note(note, note_ttype.attributes.shareDate)

