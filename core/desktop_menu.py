"""Native application menus; actions reuse the wizard's guarded UI controls."""
import json
import logging
import sys

MENU_ITEMS = [
    ('__app__', [('Buscar actualizaciones…', 'updates')]),
    ('Archivo', [('Nuevo proyecto…', 'new'), ('Abrir proyecto del historial…', 'projects'),
                 ('Archivos y logotipo personal…', 'inputs'), ('Elegir tipo de edición…', 'mode')]),
    ('Montaje', [('Parámetros de edición', 'settings'), ('Encuadres y movimiento 360', 'spherical'),
                 ('Opciones Reel', 'reel'), ('Opciones Backstage', 'backstage'), ('Opciones Medley', 'medley'),
                 ('Iniciar montaje', 'start'), ('Progreso', 'progress'), ('Revisar tomas', 'review'),
                 ('Reemplazar tomas rechazadas', 'replace'), ('Aprobar montaje Backstage', 'approve'), ('Renderizar tomas', 'render'),
                 ('Cancelar proceso…', 'cancel')]),
    ('Subtítulos y superposiciones', [('Abrir editor', 'composition'), ('Editar subtítulos / importar SRT o LRC…', 'captions'),
                 ('Detectar voz y transcribir', 'transcribe'), ('Exportar SRT', 'srt'),
                 ('Incrustar subtítulos', 'burn'), ('Imágenes, vídeos y logotipo…', 'overlays'),
                 ('Reutilizar superposiciones', 'reuse'), ('Duplicar superposición', 'duplicate')]),
    ('Reproducción', [('Reproducir / pausar en el editor', 'play'), ('Fotograma anterior', 'previousFrame'),
                      ('Fotograma siguiente', 'nextFrame'), ('Ampliar previsualización', 'expand')]),
    ('Resultado', [('Ver resultado', 'result'), ('Mostrar vídeo en el ordenador', 'finder')]),
    ('Herramientas', [('Buscar actualizaciones…', 'updates'), ('Cambiar tema claro / oscuro', 'theme'),
                     ('Detalles del proceso', 'details'), ('Copiar informe', 'report'), ('Abrir registros', 'logs')]),
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
