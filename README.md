Geverpad (An Everpad Fork) (Preview)

I use Gentoo Linux with Gnome 3 ... I wanted an easy way to take notes across platforms and my systems. Evernote has been a workable solution for me, but I could not find a good Evernote Linux client. I tried out Everpad, an Evernote client for Linux, and it seemed to fit; however, it was not working correctly for me, so I went about changing things. I have decided to dump it in a new repository, so it is searchable which I could not do with a forked version online.

Most of the documentation at https://github.com/nvbn/everpad is still valid as far as I know. The code here might not work depending what I am doing, I make changes at work via the github site and try them when I get home.

I use only Gentoo with Gnome, so it might not work on another distro.  Given this, I emerged the following packages:

dev-python/beautifulsoup
dev-python/html2text 
dev-python/httplib2 
dev-python/keyring 
dev-python/oauth2
dev-python/pyrex 
dev-python/sqlalchemy 
dev-python/dbus-python  
dev-python/setuptools
dev-python/shiboken-1.2.1-r1
dev-python/pyside-1.2.1-r1 [webkit]
sys-apps/file[python]

Then I do the following as super user:

python ./setup.py install


This is the first time I have played with python. It is neat, but I do not care much for it, no offense to anyone.
