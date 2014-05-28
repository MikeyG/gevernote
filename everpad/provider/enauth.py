#!/usr/bin/python
from gi.repository import Gtk
from gi.repository import WebKit
import urlparse  
from evernote.api.client import EvernoteClient
from keyring import get_password,set_password,delete_password

# using everpad constants for test
CONSUMER_KEY = 'nvbn-1422'
CONSUMER_SECRET = 'c17c0979d0054310'
SANDBOX_ENABLE = False


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
        #self.web_view.connect("destroy", Gtk.main_quit)
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
        elif not cb_uri.startswith('https://www.evernote.com/'):
            self.close( )
        # just do nothing this time        
        else:
            pass

        return False

# Uses Evernote client to get oauth token
def _get_evernote_token(app_debug):
    
    client = EvernoteClient(
        consumer_key=CONSUMER_KEY,
        consumer_secret =CONSUMER_SECRET,
        sandbox=SANDBOX_ENABLE
    )    

    request_token = client.get_request_token("http://everpad/")    

    if request_token['oauth_callback_confirmed']:
        url_callback = client.get_authorize_url(request_token)
        
        if app_debug:
            print ("URL:                 %s" % url_callback)
            print ("oauth_token:         %s" % request_token['oauth_token'])
            print ("oauth_token_secret:  %s" % request_token['oauth_token_secret'])
            
        window = AuthWindow(url_callback)
        window.show_all()
        Gtk.main()

        if app_debug:
            print ("oauth_verifier:      %s" % window.oauth_verifier)
                                
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
        
        if app_debug:
            print ("user_token:          %s" % user_token)
    
    elif app_debug:
        # need app error checking/message here        
        print("bad callback")    
    
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
    return get_password('geverpad', 'oauth_token')
    
#####
#  delete_auth_token( )
#
# Delete token from keyring
def delete_auth_token( ):
    delete_password('geverpad', 'oauth_token')

#####
#  change_auth( )
#
# Like original Everpad, authorize toggles token, if authorized then
# delete token, if not authorized then get token
def change_auth_token( ):
    if get_password('geverpad', 'oauth_token'):
        delete_password('geverpad', 'oauth_token')
        print "Found and deleted"
    else:
        oauth_token = _get_evernote_token(True)
        if oauth_token != "None":
            set_password('geverpad', 'oauth_token', oauth_token)


# For testing standalone
if (__name__ == '__main__'):

    change_auth_token( ) 
    
