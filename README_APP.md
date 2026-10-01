# Zucker Editor — guía incluida en la aplicación

La versión aparece en el nombre del DMG y en `build_info.json`, dentro del paquete.

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

## Datos y limpieza

Los originales, las entradas del proyecto y los exports válidos no deben borrarse como “caché”. La herramienta de limpieza solo propone temporales generados antiguos, backups sobrantes, proyectos de verificación caducados y exports históricos que no son el actual ni los dos anteriores. Los elementos se mueven a la Papelera y la auditoría es de solo lectura.

Desde el repositorio:

```bash
.venv/bin/python tools/cleanup_storage.py report
.venv/bin/python tools/cleanup_storage.py clean
```

El comando `clean` muestra primero el inventario y exige escribir `CLEANUP`. No lo ejecutes durante un render o una preparación activa.

## Identificación de builds

El nombre del DMG incluye la versión, mientras que el nombre instalable sigue siendo `Zucker Editor.app` para poder reemplazar la instalación anterior. Dentro del paquete se incluyen este README y `build_info.json`, con versión y commit.

## Soporte

Para diagnosticar un problema, conserva:

- el número de versión y commit del arranque;
- `~/ZuckerVideos/logs/`;
- el `wizard.log` del proyecto;
- el `report` del asistente.

No borres los vídeos originales para “limpiar” el proyecto.
