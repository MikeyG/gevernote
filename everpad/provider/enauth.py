#!/usr/bin/python
from gi.repository import Gtk
from gi.repository import WebKit

import urllib
import urlparse
import sys

import oauth2 as oauth
from evernote.api.client import EvernoteClient
from keyring import set_password

CONSUMER_KEY = 'nvbn-1422'
CONSUMER_SECRET = 'c17c0979d0054310'


class Browser(object):
    """ Creates a web browser using GTK+ and WebKit to authorize a
        desktop application in Evernote. It uses OAuth 2.0.
        Requires the evernote.api.client to be installed. 
    """

    def __init__(self, url):
        """ 
            Constructor. Creates the GTK+ app and adds the WebKit widget
            
            @param url 
        """
        self.close_window = True
        self.url = url
        self.oauth_verifier = ''
        
        # Creates the GTK+ app
        # http://www.pygtk.org/pygtk2reference/class-gtkwindow.html
        # gtk.Window - a top-level window that holds one child widget.
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Evernote Authorize")
        self.window.set_default_size(800,600)
        
        self.scrolled_window = Gtk.ScrolledWindow()
        
        # Creates a WebKit view
        self.web_view = WebKit.WebView()
        self.scrolled_window.add(self.web_view)
        self.window.add(self.scrolled_window)

        # Connects events
        self.window.connect('destroy', self._destroy_event_cb) # Close window
        self.web_view.connect('load-committed', self._load_committed_cb) # Load page
        # change size !!!!!!!
        #self.window.set_default_size(1024, 800)

        # Loads the Evernote OAuth page
        self.web_view.load_uri(urllib.quote(url))

    def _load_committed_cb(self, web_view, frame):
        """ Callback. The page is about to be loaded. This event is captured
            to intercept the OAuth 2.0 redirection, which includes the
            access token.

            @param web_view A reference to the current WebKitWebView.
            @param frame A reference to the main WebKitWebFrame.
        """
        # Gets the current URL to check whether is the one of the redirection
        uri = frame.get_uri()        
        self.oauth_verifier = uri 

        # Finish        
        Gtk.main_quit()  

        if self.close_window:
            try:
                self.window.destroy()
            except RuntimeError:
                pass

    def _destroy_event_cb(self, widget):
        """ Callback for close window. Closes the application. """
        return Gtk.main_quit()

    def authorize(self):
        """ Runs the app. """
 
        # display the window
        self.window.show_all()

        # start the GTK+ processing loop which we quit 
        # when the window is closed
        Gtk.main()

if (__name__ == '__main__'):
    
    client = EvernoteClient(
        consumer_key=CONSUMER_KEY,
        consumer_secret =CONSUMER_SECRET,
        sandbox=False
    )

    request_token = client.get_request_token("http://everpad/")
    url = client.get_authorize_url(request_token)

    if request_token['oauth_callback_confirmed']:
        # Creates the browser
        browser = Browser(url)
    else:
        # need app error checking/message here        
        print("bad callback")

    # Launch browser window
    browser.authorize()

    return_token = dict(urlparse.parse_qsl(browser.oauth_verifier))

#    returned_token = self.client.get_access_token(
#        request_token['oauth_token'],
#        request_token['oauth_token_secret'],
#        browser.oauth_verifier
#    )

    
    # Token available?
    print "Token: %s" % return_token
 

    # set_password('everpad', 'oauth_token', returned_token)

    

