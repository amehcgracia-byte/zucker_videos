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


MODE_ACTIONS = {
    'spherical': {'youtube', '360'}, 'reel': {'reel'},
    'backstage': {'backstage'}, 'medley': {'medley'},
    'approve': {'backstage'}, 'review': {'youtube', 'reel', '360'},
    'replace': {'youtube', 'reel', '360'}, 'render': {'youtube', 'reel', '360'},
    **{action: {'reel', 'backstage', '360'} for action in
       ('composition', 'captions', 'srt', 'burn', 'overlays', 'reuse', 'duplicate',
        'play', 'previousFrame', 'nextFrame', 'expand')},
    'transcribe': {'reel', 'backstage'},
}


def apply_menu_mode(window, mode):
    """Hide inapplicable native actions, preserving the OS Edit menu."""
    allowed = {label: mode in MODE_ACTIONS[action] for _, items in MENU_ITEMS
               for label, action in items if action in MODE_ACTIONS}
    try:
        if sys.platform == 'darwin':
            from PyObjCTools.AppHelper import callAfter
            from AppKit import NSApplication
            def update():
                def visit(menu):
                    if menu is None:
                        return
                    for item in menu.itemArray():
                        if item.title() in allowed:
                            item.setHidden_(not allowed[item.title()])
                        if item.submenu():
                            visit(item.submenu())
                            if item.title() in {'Captions and Overlays', 'Playback'}:
                                item.setHidden_(mode not in {'reel', 'backstage', '360'})
                visit(NSApplication.sharedApplication().mainMenu())
            callAfter(update)
        elif sys.platform == 'win32':
            from System import Action
            def update():
                def visit(items):
                    for item in items:
                        if str(item.Text) in allowed:
                            item.Available = allowed[str(item.Text)]
                        if hasattr(item, 'DropDownItems'):
                            visit(item.DropDownItems)
                            if str(item.Text) in {'Captions and Overlays', 'Playback'}:
                                item.Available = mode in {'reel', 'backstage', '360'}
                if window.native.MainMenuStrip:
                    visit(window.native.MainMenuStrip.Items)
            window.native.Invoke(Action(update))
    except Exception:
        logging.getLogger(__name__).exception('Could not apply desktop menu mode')
