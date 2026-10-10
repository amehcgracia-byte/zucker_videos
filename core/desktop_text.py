"""Restore native WebView text menus without enabling debug/devtools."""
import logging
import sys


def enable_native_text_menu(window):
    try:
        if sys.platform == 'darwin':
            import objc
            from PyObjCTools.AppHelper import callAfter
            from webview.platforms.cocoa import BrowserView

            def preserve_menu(self, menu, event):
                # WKWebView has already populated context-sensitive native items.
                # pywebview's release handler normally removes every one of them.
                pass

            def install():
                objc.classAddMethods(BrowserView.WebKitHost, [objc.selector(
                    preserve_menu, selector=b'willOpenMenu:withEvent:', signature=b'v@:@@')])
            callAfter(install)
        elif sys.platform == 'win32':
            from System import Action
            def install():
                window.native.webview.CoreWebView2.Settings.AreDefaultContextMenusEnabled = True
            window.native.Invoke(Action(install))
    except Exception:
        logging.getLogger(__name__).exception('Could not enable native text context menu')
