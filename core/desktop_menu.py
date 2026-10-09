"""Native application menus; actions reuse the wizard's guarded UI controls."""
import json
import logging
import sys

MENU_ITEMS = [
    ('__app__', [('Check for Updates…', 'updates')]),
    ('File', [('New Project…', 'new'), ('Open Existing Project…', 'projects'),
              ('Files and Personal Logo…', 'inputs'), ('Choose Edit Type…', 'mode')]),
    ('Video', [('Edit Parameters', 'settings'), ('360 Framing and Motion', 'spherical'),
               ('Reel Options', 'reel'), ('Backstage Options', 'backstage'), ('Medley Options', 'medley'),
               ('Start Edit', 'start'), ('Progress', 'progress'), ('Review Shots', 'review'),
               ('Replace Rejected Shots', 'replace'), ('Approve Backstage Edit', 'approve'), ('Render Shots', 'render'),
               ('Cancel Process…', 'cancel')]),
    ('Captions and Overlays', [('Open Editor', 'composition'), ('Edit Captions / Import SRT or LRC…', 'captions'),
              ('Detect Speech and Transcribe', 'transcribe'), ('Export SRT', 'srt'),
              ('Burn In Captions', 'burn'), ('Images, Videos and Logo…', 'overlays'),
              ('Reuse Overlays', 'reuse'), ('Duplicate Overlay', 'duplicate')]),
    ('Playback', [('Play / Pause in Editor', 'play'), ('Previous Frame', 'previousFrame'),
                  ('Next Frame', 'nextFrame'), ('Enlarge Preview', 'expand')]),
    ('Result', [('View Result', 'result'), ('Show Video in Finder', 'finder')]),
    ('Help', [('Tutorial with Einstein…', 'tutorial')]),
    ('Tools', [('Check for Updates…', 'updates'), ('Toggle Light / Dark Theme', 'theme'),
               ('Process Details', 'details'), ('Copy Report', 'report'), ('Open Logs', 'logs')]),
]


def build_desktop_menu(window):
    from webview.menu import Menu, MenuAction

    def dispatch(action):
        try:
            window.evaluate_js('window.EditorMenu && window.EditorMenu.run(' + json.dumps(action) + ')')
        except Exception:
            logging.getLogger(__name__).exception('Menu action failed: %s', action)

    return [Menu(title, [MenuAction(label, lambda action=action: dispatch(action))
                        for label, action in items]) for title, items in MENU_ITEMS
            if title != '__app__' or sys.platform == 'darwin']


def hide_external_services():
    """Hide OS-provided assistant services here, without changing global preferences."""
    if sys.platform != 'darwin':
        return
    from PyObjCTools.AppHelper import callAfter

    def remove():
        from AppKit import NSApplication
        app = NSApplication.sharedApplication()
        main = app.mainMenu()
        if not main or not main.numberOfItems():
            return
        menu = main.itemAtIndex_(0).submenu()
        for item in list(menu.itemArray()):
            if item.submenu() and item.submenu() == app.servicesMenu():
                menu.removeItem_(item)
    callAfter(remove)
