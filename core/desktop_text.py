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


def enable_named_microphone_permission(window):
    """Keep consent explicit, but identify the desktop app instead of localhost."""
    if sys.platform != 'darwin':
        return
    import objc
    import WebKit
    from AppKit import NSAlert, NSAlertFirstButtonReturn
    from PyObjCTools.AppHelper import callAfter
    from urllib.parse import urlsplit
    from webview.platforms.cocoa import BrowserView

    selector = b'webView:requestMediaCapturePermissionForOrigin:initiatedByFrame:type:decisionHandler:'
    objc.registerMetaDataForSelector(b'BrowserDelegate', selector, {
        'arguments': {6: {'callable': {
            'retval': {'type': b'v'},
            'arguments': {0: {'type': b'^v'}, 1: {'type': b'q'}}}}}})

    def request_permission(self, view, origin, frame, capture_type, handler):
        current = urlsplit(str(view.URL().absoluteString()))
        local = (current.hostname in {'127.0.0.1', 'localhost'}
                 and str(origin.host()) == current.hostname
                 and int(origin.port()) == current.port
                 and str(origin.protocol()) == current.scheme
                 and bool(frame.isMainFrame()))
        if not local or capture_type != WebKit.WKMediaCaptureTypeMicrophone:
            handler(WebKit.WKPermissionDecisionDeny)
            return
        alert = NSAlert.alloc().init()
        alert.setMessageText_('¿Permitir que Zucker Editor use el micrófono?')
        alert.setInformativeText_('Se grabará tu voz para crear captions. La grabación termina al pulsar Stop and transcribe.')
        alert.addButtonWithTitle_('Permitir')
        alert.addButtonWithTitle_('No permitir')
        allowed = alert.runModal() == NSAlertFirstButtonReturn
        handler(WebKit.WKPermissionDecisionGrant if allowed else WebKit.WKPermissionDecisionDeny)

    def install():
        objc.classAddMethods(BrowserView.BrowserDelegate, [objc.selector(
            request_permission, selector=selector, signature=b'v@:@@@q@?')])
    callAfter(install)
