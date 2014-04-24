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
class Notebook(Base):
    __tablename__ = 'notebooks'
    id = Column(Integer, primary_key=True)
    guid = Column(String)
    name = Column(String)
    default = Column(Boolean)
    service_created = Column(Integer)
    service_updated = Column(Integer)
    action = Column(Integer)
    stack = Column(String)

    def from_api(self, notebook):
        """Fill data from api"""
        self.name = notebook.name.decode('utf8')
        self.default = notebook.defaultNotebook
        self.service_created = notebook.serviceCreated
        self.service_updated = notebook.serviceUpdated
        self.action = const.ACTION_NONE
        if notebook.stack:
            self.stack = notebook.stack.decode('utf8')

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
# Notebook ORM class to save tag specific place data to 
# the database
class Tag(Base):
    __tablename__ = 'tags'
    id = Column(Integer, primary_key=True)
    guid = Column(String)
    name = Column(String)
    parentGuid = Column(String)
    action = Column(Integer)

    def from_api(self, tag):
        """Fill data from api"""
        self.name = tag.name.decode('utf8')
        self.parentGuid = tag.parentGuid
        self.action = const.ACTION_NONE


# *************************************************************
# Notebook ORM class to save resource specific place data to 
# the database
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

#        with open(self.file_path, 'w') as data:
#            data.write(resource.data.body)


# *************************************************************
# Notebook ORM class to save note specific place data to the 
# database
class Place(Base):
    __tablename__ = 'places'
    id = Column(Integer, primary_key=True)
    name = Column(String)

# *************************************************************
# Notebook ORM class to save sync specific data to the database
class Sync(Base):
    __tablename__ = 'sync'
    id = Column(Integer, primary_key=True)
    update_count = Column(Integer)          # current client count
    srv_update_count = Column(Integer)      # current server count    
    last_sync = Column(Integer)
    virgin_db = Column(Integer)
    # MKG:  Think I am going to track rate limit here    
    rate_limit = Column(Integer)
    rate_limit_time = Column(Integer)
    connect_error_count = Column(Integer)
 
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










