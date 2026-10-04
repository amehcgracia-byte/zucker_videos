# Zucker Editor — guía incluida en la aplicación

La versión y el commit aparecen en la aplicación y en `build_info.json`, dentro del paquete. El instalador macOS habitual se llama `Zucker Editor.dmg`.

Zucker Editor convierte audio de una sesión y grabaciones de cámara en un vídeo editado. El flujo normal es:

1. Añadir vídeo(s), audio y, cuando corresponda, `songs.json`.
2. Elegir el modo.
3. Revisar los frames y dejar que se genere el export.
4. Abrir el resultado desde la carpeta de exports.

## Modos

- **YouTube**: export horizontal 16:9 de larga duración. No aplica captions ni flyer.
- **Reel**: export vertical corto con captions/flyer opcionales.
- **Backstage**: flujo documental con revisión de cues y captions cuando se solicitan.
- **360**: acepta un vídeo 360 con audio incrustado o un master separado; conserva la relación preview → render y permite controles de encuadre.

Un proyecto nuevo siempre crea una carpeta `.zuckervid` nueva. Abrir un proyecto anterior es una acción explícita desde la bandeja de proyectos; nunca se selecciona automáticamente por coincidir las rutas de entrada.

## Frames y tiempos de audio

Cada proyecto conserva su biblioteca de alternativas. «Otro frame» reemplaza solo la toma elegida y mantiene las otras miniaturas. Si no hay otra cámara o vista que cubra ese intervalo, se informa en la tarjeta. Durante una exportación hay que esperar o cancelarla antes de cambiar tomas.

El inicio y el final elegidos limitan el audio del máster. Las cabeceras no añaden audio anterior ni posterior a ese intervalo. En una edición sincronizada, el audio seleccionado comienza con las imágenes correspondientes; las zonas de logo sin contenido seleccionado quedan en silencio.

## Windows

El repositorio incluye `tools/build_windows.ps1`, que genera un ZIP con `Zucker Editor <versión>.exe` y sus recursos. El ZIP debe extraerse completo antes de abrir el EXE; no basta con copiar solo el ejecutable. Se necesita FFmpeg/FFprobe disponible en PATH. La existencia del script no implica que haya un EXE publicado: cada entrega Windows debe comprobarse por versión y commit.

## Datos y limpieza

Los originales, las entradas del proyecto y los exports válidos no deben borrarse como “caché”. La herramienta de limpieza solo propone temporales generados antiguos, backups sobrantes, proyectos de verificación caducados y exports históricos que no son el actual ni los dos anteriores. Los elementos se mueven a la Papelera y la auditoría es de solo lectura.

Desde el repositorio:

```bash
.venv/bin/python tools/cleanup_storage.py report
.venv/bin/python tools/cleanup_storage.py clean
```

El comando `clean` muestra primero el inventario y exige escribir `CLEANUP`. No lo ejecutes durante un render o una preparación activa.

## Identificación de builds

El instalador macOS se llama `Zucker Editor.dmg`; la versión identifica el volumen montado. El nombre instalable sigue siendo `Zucker Editor.app` para poder reemplazar la instalación anterior. Dentro del paquete se incluyen este README y `build_info.json`, con versión y commit.

## Soporte

Para diagnosticar un problema, conserva:

- el número de versión y commit del arranque;
- `~/ZuckerVideos/logs/`;
- el `wizard.log` del proyecto;
- el `report` del asistente.

No borres los vídeos originales para “limpiar” el proyecto.
