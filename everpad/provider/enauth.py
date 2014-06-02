from gi.repository import Gtk
from gi.repository import WebKit
import urlparse  
from evernote.api.client import EvernoteClient
from keyring import get_password,set_password,delete_password
from everpad.const import (
    CONSUMER_KEY, CONSUMER_SECRET, HOST,
)
# python built-in logging 
import logging

logger = logging.getLogger('gevernote-provider')

class AuthWindow(Gtk.Window):
    def __init__(self, url_callback):
        super(AuthWindow, self).__init__()
        
        self.url_callback = url_callback
        self.oauth_verifier = 'None'
        
        # Creates the GTK+ app and a WebKit view
        self.web_view = WebKit.WebView()
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.add(self.web_view)
        self.add(self.scrolled_window)
        self.set_size_request(800, 600)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_title("Authorize")
        self.set_skip_taskbar_hint(True)
        self.set_resizable(False)

        # http://midori-browser.org/docs/api/vala/midori/WebKit.WebView.html        
        self.web_view.connect('navigation-policy-decision-requested', self.webkit_navigation_callback)
        self.connect("delete-event", Gtk.main_quit)
        
        self.web_view.load_uri(url_callback)

    def webkit_navigation_callback(self, 
       web_view, frame, request,
       navigation_action, policy_decision, *args
    ):
        
        cb_uri = request.get_uri( ) 
        
        # check if this is the verifier        
        if "everpad" and "oauth_verifier" in cb_uri:
            if self.oauth_verifier == "None":
                parsed_uri = dict(urlparse.parse_qsl(cb_uri))
                self.oauth_verifier = parsed_uri['oauth_verifier']
                self.close( )
        # easy way to handle a cancel button on auth page        
        elif not cb_uri.startswith(HOST):
            self.close( )
        # just do nothing this time        
        else:
            pass

        return False

# Uses Evernote client to get oauth token
def _get_evernote_token( ):

    logger.info("Authorizing")
    
    client = EvernoteClient(
        consumer_key=CONSUMER_KEY,
        consumer_secret=CONSUMER_SECRET,
        sandbox=False
    )    

    request_token = client.get_request_token("http://everpad/")    

    if request_token['oauth_callback_confirmed']:
        url_callback = client.get_authorize_url(request_token)
        
        logger.debug("URL:                 %s" % url_callback)
        logger.debug("oauth_token:         %s" % request_token['oauth_token'])
        logger.debug("oauth_token_secret:  %s" % request_token['oauth_token_secret'])
            
        window = AuthWindow(url_callback)
        window.show_all()
        Gtk.main()

        logger.debug("oauth_verifier:      %s" % window.oauth_verifier)
                                
        if not (window.oauth_verifier == "None"):
            # get the token for authorization     
            user_token = client.get_access_token(
                request_token['oauth_token'],
                request_token['oauth_token_secret'],
                window.oauth_verifier
            )
        else:
            # handle window closed by cancel and no token            
            user_token = window.oauth_verifier	
        	        
        Gtk.main_quit
        
        logger.debug("user_token:          %s" % user_token)
    
    elif app_debug:
        # need app error checking/message here        
        logger.debug("bad callback")    
    
    # Token available?
    return user_token

###############################################################
#
#            External Authorization Routines
#
###############################################################

#####
#  get_auth( )
#
# Return true if token exists
def get_auth_token( ):
    logger.debug("enauth: Auth check")
    return get_password('geverpad', 'oauth_token')
    
#####
#  delete_auth_token( )
#
# Delete token from keyring
def delete_auth_token( ):
    if get_password('geverpad', 'oauth_token'):
        logger.debug("enauth: Removing token")
        delete_password('geverpad', 'oauth_token')

#####
#  change_auth( )
#
# Like original Everpad, authorize toggles token, if authorized then
# delete token, if not authorized then get token
def change_auth_token( ):
    oauth_token = _get_evernote_token( )
    if oauth_token != "None":
        set_password('geverpad', 'oauth_token', oauth_token)
        logger.debug("enauth: Token saved")
    else:
        logger.debug("enauth: Token not saved")
        
    return oauth_token


    
