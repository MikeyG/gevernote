from BeautifulSoup import BeautifulSoup
from sqlalchemy import Table, Column, Integer, ForeignKey, String, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.exc import NoResultFound
from ..tools import prepare_file_path
from .. import const
import binascii
import os
import urllib
import json
import dbus
import socket


# The declarative_base() callable returns a new base class from 
# which all mapped classes should inherit. When the class definition is 
# completed, a new Table and mapper() will have been generated.
# http://docs.sqlalchemy.org/en/rel_0_9/orm/extensions/declarative.html
Base = declarative_base()

# engine = create_engine('sqlite:///%s' % db_path)
# in tools.py

notetags_table = Table(
    'notetags', Base.metadata,
    Column('note', Integer, ForeignKey('notes.id')),
    Column('tag', Integer, ForeignKey('tags.id'))
)


# *********************************************************
# Note ORM class to save note specific data to the database
#
# Struct: Note
# guid Guid 
# title string 
# content string 
# contentHash string 
# contentLength i32 
# created Timestamp 
# updated Timestamp 
# deleted Timestamp 
# active bool 
# -- not used updateSequenceNum i32 
# notebookGuid string 
# tagGuids list<Guid> 
# resources list<Resource> 
# --- Maybe use later - attributes NoteAttributes 
# -- not used tagNames list<string> 

class Note(Base):
    __tablename__ = 'notes'
    id = Column(Integer, primary_key=True)
    guid = Column(String)
    title = Column(String)
    content = Column(String)
    
    # MKG added for playing
    contentHash = Column(String)   
    contentLength = Column(Integer)
    
    created = Column(Integer)
    updated = Column(Integer)
    
    
    #deleted
    #active
    
    updated_local = Column(Integer)
    notebook_id = Column(Integer, ForeignKey('notebooks.id'))
    notebook = relationship("Notebook", backref='note')
    tags = relationship(
        "Tag",
        secondary=notetags_table,
        backref="notes",
    )
    pinnded = Column(Boolean, default=False)
    resources = relationship("Resource")
    place_id = Column(Integer, ForeignKey('places.id'))
    place = relationship("Place", backref='note')
    action = Column(Integer)
    conflict_parent = relationship("Note", post_update=False)
    conflict_parent_id = Column(
        Integer, ForeignKey('notes.id'), nullable=True,
    )

    # sharing data:
    share_date = Column(Integer)
    share_status = Column(Integer, default=const.SHARE_NONE)
    share_url = Column(String)


    # not real good with @property in python
    # following are getters/setters


    # -- get/set note's tags????
    @property
    def tags_dbus(self):
        return map(lambda tag: tag.name, self.tags)

    @tags_dbus.setter
    def tags_dbus(self, val):
        tags = []
        for tag in val:
            if tag and tag != ' ':  # for blank array and other
                try:
                    tags.append(self.session.query(Tag).filter(
                        (Tag.name == tag)
                        & (Tag.action != const.ACTION_DELETE)
                    ).one())
                except NoResultFound:
                    tg = Tag(name=tag, action=const.ACTION_CREATE)
                    self.session.add(tg)
                    tags.append(tg)
        self.tags = tags

    # -- get/set note's notebook id
    @property
    def notebook_dbus(self):
        if self.notebook:
            return self.notebook.id
        else:
            return self.session.query(Notebook).filter(
                Notebook.default == True,
            ).one().id

    @notebook_dbus.setter
    def notebook_dbus(self, val):
        try:
            self.notebook = self.session.query(Notebook).filter(
                Notebook.id == val,
            ).one()
        except NoResultFound:
            self.notebook = self.session.query(Notebook).filter(
                Notebook.default == True,
            ).one()

    # -- get/set note's place
    @property
    def place_dbus(self):
        if self.place:
            return self.place.name
        return ''

    @place_dbus.setter
    def place_dbus(self, val):
        if val:
            self.set_place(val, self.session)

    # -- get/set note's conflict parent id
    @property
    def conflict_parent_dbus(self):
        if self.conflict_parent_id:
            return self.conflict_parent_id
        return 0

    @conflict_parent_dbus.setter
    def conflict_parent_dbus(self, val):
        pass

    # -- get/set note's conflict item???
    @property
    def conflict_items_dbus(self):
        return map(
            lambda item: item.id,
            self.session.query(Note).filter(
                Note.conflict_parent_id == self.id,
            ).all(),
        ) or dbus.Array([], signature='i')

    @conflict_items_dbus.setter
    def conflict_items_dbus(self, val):
        pass

    # -- get/set note's share date
    @property
    def share_date_dbus(self):
        return self.share_date or 0

    @share_date_dbus.setter
    def share_date_dbus(self, val):
        pass

    # -- get/set note's share url
    @property
    def share_url_dbus(self):
        return self.share_url or ''

    @share_url_dbus.setter
    def share_url_dbus(self, val):
        pass
    
    # stuff the database with the note values
    # passed note and database session
    def from_api(self, note, session):
        """Fill data from api"""
        
        # handle note content 
        soup = BeautifulSoup(note.content.decode('utf8'))
        content = reduce(
            lambda txt, cur: txt + unicode(cur),
            soup.find('en-note').contents, u'',
        )
        
        # record stuffing ...
        self.title = note.title.decode('utf8')
        self.content = content
        self.created = note.created
        self.updated = note.updated
        self.action = const.ACTION_NONE
        
        # shouldn't there always be a notebook guid????
        if note.notebookGuid:
            self.notebook = session.query(Notebook).filter(
                Notebook.guid == note.notebookGuid,
            ).one()
            
        # note tags    
        if note.tagGuids:
            self.tags = session.query(Tag).filter(
                Tag.guid.in_(note.tagGuids),
            ).all()
        
        # handle places ....
        #
        # Allows the user to assign a human-readable location name associated with a note. Users 
        # may assign values like 'Home' and 'Work'. Place names may also be populated with values 
        # from geonames database (e.g., a restaurant name). Applications are encouraged to normalize
        # values so that grouping values by place name provides a useful result. Applications MUST 
        # NOT automatically add place name values based on geolocation without confirmation from the 
        # user; that is, the value in this field should be more useful than a simple automated lookup 
        # based on the note's latitude and longitude. 
        place_name = None
        if getattr(note, 'attributes'):
            if note.attributes.placeName:
                place_name = note.attributes.placeName.decode('utf8')
            elif note.attributes.longitude:
                try:
                    data = json.loads(urllib.urlopen(
                        'http://maps.googleapis.com/maps/api/geocode/json?latlng=%.4f,%.4f&sensor=false' % (
                            note.attributes.latitude,
                            note.attributes.longitude,
                        ),
                    ).read())
                    try:
                        place_name = data['results'][0]['formatted_address']
                    except (IndexError, KeyError):
                        pass
                except socket.error:
                    pass
        if place_name:
            self.set_place(place_name, session)
        
        # end of stuffin :)
        
    # just a local to set places
    def set_place(self, name, session):
        try:
            place = session.query(Place).filter(
                Place.name == name,
            ).one()
        except NoResultFound:
            place = Place(name=name)
            session.add(place)
        self.place = place


# *************************************************************
# Notebook ORM class to save note specific data to the database
#
# Struct: Notebook
#   guid Guid 
#   name string 
#   updateSequenceNum i32 
#   defaultNotebook bool 
#   serviceCreated Timestamp 
#   serviceUpdated Timestamp 
#   * Maybe look at this later -publishing 
#   * Maybe look at this later -published 
#   stack string 
#   * Maybe look at this later -sharedNotebooks 
#   * Not used -businessNotebook 
#   * Not used -contact 
#   * Maybe look at this later -restrictions

class Notebook(Base):
    __tablename__ = 'notebooks'
    id = Column(Integer, primary_key=True)
    guid = Column(String)
    name = Column(String)
    usn = Column(Integer)
    default = Column(Boolean)
    service_created = Column(Integer)
    service_updated = Column(Integer)
    stack = Column(String)

    # local use
    action = Column(Integer)

    # Generate database notebook record
    def from_api(self, notebook):
        """Fill data from api"""
        self.name = notebook.name.decode('utf8')
        self.usn = notebook.updateSequenceNum
        self.default = notebook.defaultNotebook
        self.service_created = notebook.serviceCreated
        self.service_updated = notebook.serviceUpdated
        if notebook.stack:
            self.stack = notebook.stack.decode('utf8')

        self.action = const.ACTION_NONE

    # -- get/set notebook's stack date
    @property
    def stack_dbus(self):
        if self.stack:
            return self.stack
        return ''

    @stack_dbus.setter
    def stack_dbus(self, val):
        self.stack = val


# *************************************************************
# Tag ORM class to save tag specific place data to the database
#
# Struct: Tag
# guid Guid 
# name string 
# parentGuid Guid 
# -- Not used - updateSequenceNum i32 

class Tag(Base):
    __tablename__ = 'tags'
    id = Column(Integer, primary_key=True)
    guid = Column(String)
    name = Column(String)
    parentGuid = Column(String)

    # local use
    action = Column(Integer)

    def from_api(self, tag):
        """Fill data from api"""
        self.name = tag.name.decode('utf8')
        self.parentGuid = tag.parentGuid
        self.action = const.ACTION_NONE

# *************************************************************
# Searches ORM class to save data to the database
#
# Struct: LinkedNotebook
# shareName	string
# username	string
# shardId	string
# shareKey	string
# uri	string
# guid	Guid
# noteStoreUrl	string
# stack	string
# businessId	i32

class LNB(Base):
    __tablename__ = 'linkednotebook'
    id = Column(Integer, primary_key=True)
    guid = Column(String)
    shareName = Column(String)
    username = Column(String)
    shardId = Column(String)
    shareKey = Column(String)
    uri = Column(String)
    noteStoreUrl = Column(String)
    stack = Column(String) 
    businessId = Column(Integer) 
    # local use
    action = Column(Integer)    

    def from_api(self, linkednb):
        """Fill data from api"""
        
        self.guid = linkednb.guid
        self.shareName = linkednb.shareName
        self.username = linkednb.username
        self.shardId = linkednb.shardId
        self.shareKey = linkednb.shareKey
        self.uri = linkednb.uri
        self.noteStoreUrl = Column(String)
                
        if linkednb.stack:
            self.stack = linkednb.stack.decode('utf8')        
        # self.businessId = Column(Integer) 
        
        self.action = const.ACTION_NONE

    
# *************************************************************
# Searches ORM class to save data to the database
#
# Struct: SavedSearch
# guid Guid 
# name string 
# query string 
# format QueryFormat 
# updateSequenceNum i32 
# scope SavedSearchScope 

class Searches(Base):
    __tablename__ = 'searches'
    id = Column(Integer, primary_key=True)
    guid = Column(String)
    name = Column(String)
    query = Column(String)
    queryformat = Column(Integer)

    # local use
    action = Column(Integer)

    def from_api(self, savsearch):
        """Fill data from api"""
        
        self.guid = savsearch.guid
        self.name = savsearch.name
        self.query = savsearch.query

        self.action = const.ACTION_NONE
        
        
# *************************************************************
# Resource ORM class to save resource specific place data to the database
#
# Struct: Resource
# guid Guid 
# noteGuid Guid 
# data Data  -- bodyHash string
#            |- size i32
#            -- body string
# mime string 
# -- look at later - width i16 
# -- look at later - height i16 
# -- Not used yet - recognition Data 
# -- Not used yet - attributes ResourceAttributes 
# -- Not used updateSequenceNum i32 
# -- Not used alternateData Data 

class Resource(Base):
    __tablename__ = 'resources'
    id = Column(Integer, primary_key=True)
    note_id = Column(Integer, ForeignKey('notes.id'))
    file_name = Column(String)
    file_path = Column(String)
    guid = Column(String)
    hash = Column(String)
    mime = Column(String)
    action = Column(Integer)

    def from_api(self, resource):
        """Fill data from api"""
        if resource.attributes.fileName:
            self.file_name = resource.attributes.fileName.decode('utf8')
        else:
            self.file_name = resource.guid.decode('utf8')
        self.hash = binascii.b2a_hex(resource.data.bodyHash)
        self.action = const.ACTION_NONE
        self.mime = resource.mime.decode('utf8')
        path = os.path.expanduser('~/.everpad/data/%s/' % self.note_id)
        
        # MKG - okay here is where my problem was - the resource binary
        # had not been pulled - an API change?

        # seems like a sound idea to just make the directory here and 
        # pull/save the resource binary back in note.py
        # Note to self:  Are empty data directories removed when 
        # all resources are deleted? Is there a check?
        try:
            os.mkdir(path)
        except OSError:
            pass
        self.file_path = prepare_file_path(path, self.file_name)


# ***************************************************************
# Place ORM class to save note specific place data to the database
#
# Struct: 

class Place(Base):
    __tablename__ = 'places'
    id = Column(Integer, primary_key=True)
    name = Column(String)

# *************************************************************
# Internal table for provider use
#
class Sync(Base):
    __tablename__ = 'sync'
    id = Column(Integer, primary_key=True)
    
    # local counts
    update_count = Column(Integer)          # USN current client count
    last_sync = Column(Integer)             # Last error free sync
    need_full_sync = Column(Integer)

    # server sync info returned from getSyncState
    srv_current_time = Column(Integer)      # Current server time at call
    srv_update_count = Column(Integer)      # USN current server count    
    srv_uploaded_bytes = Column(Integer)    # Just because
    srv_fullSyncBefore = Column(Integer)    # Date/Time before next full sync

# *************************************************************
# Me playing - future
class Account(Base):
    __tablename__ = 'account'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    username = Column(String)
    email = Column(String)
    created = Column(Integer)
    updated = Column(Integer)
    deleted = Column(Integer)
    active = Column(Integer)










