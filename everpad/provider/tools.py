#from thrift.protocol import TBinaryProtocol
#from thrift.transport import THttpClient
#from evernote.edam.userstore import UserStore
#from evernote.edam.notestore import NoteStore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from urlparse import urlparse
from .models import Base
#from ..const import HOST, DB_PATH
from ..tools import get_proxy_config
#from ..specific import get_keyring
import os

from everpad.const import (
    CONSUMER_KEY, CONSUMER_SECRET,
    DB_PATH,
)
from evernote.api.client import EvernoteClient

# Pull in all the keyring calls
from keyring import get_password, set_password, delete_password


# change item to lower case
# used local only
def _nocase_lower(item):
    return unicode(item).lower()

# **********************************************************
#               Keyring calls
#
# access the system keyring service
# ref: https://pypi.python.org/pypi/keyring

# set_password() - specific.py
# set_password(service, username, password)
# Store the password in the keyring.
# Used local and agent.py - _init_network
def set_auth_token(token):
    set_password('everpad', 'oauth_token', token)

# get_keyring()
# Returns the password stored in the active keyring. 
# If the password does not exist, it will return None.
# Used local and agent.py - _init_network
def get_auth_token():
    return get_password('everpad', 'oauth_token')

# delete_password( )
# Remove token from key ring
def delete_token(token):
    delete_password('everpad', 'oauth_token')
    
"""
This is the start of my next change.  I want to move the entire oauth to
here, tools.py.  A call to auth_geverpad_token will authorize and get token.
I want to do this so provider handles all things evernote and the user programs -
i.e. indicator ... pad do not talk to evernote.
"""
def auth_geverpad_token():
# •Temporary credential request URI: https://evernoteHost/oauth
# •Resource owner authorization URI: https://evernoteHost/OAuth.action
# •Token request URI: https://evernoteHost/oauth
# •Security: HTTPS for all requests
# •Supported signature methods: PLAINTEXT & HMAC-SHA1
# •Supported OAuth parameter locations: HTTP Authorization header & request URI query parameters
# http://tools.ietf.org/html/rfc5849

    client = EvernoteClient(
        consumer_key=CONSUMER_KEY,
        consumer_secret =CONSUMER_SECRET,
        sandbox=False
    )
    
    # request_token
    #   -oauth_token
    #   -oauth_token_secret
    #   -oauth_callback_confirmed
    request_token = client.get_request_token("http://everpad/")
    
    # https://www.evernote.com/OAuth.action?oauth_token=<token>
    url = client.get_authorize_url(request_token)
    
    return request_token, url


# Setup database
# Ref:  http://docs.sqlalchemy.org/en/rel_0_9/orm/tutorial.html
#       http://pypix.com/tools-and-tips/essential-sqlalchemy/
def get_db_session(db_path=None):
    # DB_PATH defined in const.py
    if not db_path:
        db_path = os.path.expanduser(DB_PATH)
    # Ex: engine = create_engine('sqlite:///:memory:', echo=True)
    # echo True - logging to python
    # uses mysql-python as the default DBAPI
    engine = create_engine('sqlite:///%s' % db_path, echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    conn = session.connection()
    conn.connection.create_function('lower', 1, _nocase_lower)
    return session
