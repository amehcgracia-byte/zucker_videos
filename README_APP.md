# Zucker Editor — guía incluida en la aplicación

La versión y el commit aparecen en la aplicación y en `build_info.json`, dentro del paquete. El instalador macOS habitual se llama `Zucker Editor.dmg`.

Zucker Editor convierte audio de una sesión y grabaciones de cámara en un vídeo editado. El flujo normal es:

1. Pulsar New Project y elegir el modo en la ventana de Einstein.
2. Añadir los archivos que pide ese modo y pulsar Continue.
3. Elegir la carpeta del proyecto; queda guardado antes de abrir los parámetros.
4. Configurar solo las opciones de ese modo, revisar los frames cuando corresponda y generar el vídeo.
5. Abrir el resultado desde la carpeta de exports.

## Modos

- **YouTube**: export horizontal 16:9 de larga duración. No aplica captions ni flyer.
- **Reel**: export vertical corto con captions/flyer opcionales.
- **Backstage**: flujo documental con revisión de cues y captions cuando se solicitan.
- **Medley Populi**: extractos de varias canciones, con audio original, audio externo opcional o tomas silenciosas.
- **360**: acepta un vídeo 360 con audio incrustado o un master separado; conserva la relación preview → render y permite controles de encuadre.

Un proyecto nuevo siempre crea una carpeta `.zuckervid` nueva. Abrir un proyecto anterior es una acción explícita desde la bandeja de proyectos; nunca se selecciona automáticamente por coincidir las rutas de entrada.

## Frames y tiempos de audio

Cada proyecto conserva su biblioteca de alternativas. «Otro frame» reemplaza solo la toma elegida y mantiene las otras miniaturas. Si no hay otra cámara o vista que cubra ese intervalo, se informa en la tarjeta. Durante una exportación hay que esperar o cancelarla antes de cambiar tomas.

El inicio y el final elegidos limitan el audio del máster. Las cabeceras no añaden audio anterior ni posterior a ese intervalo. En una edición sincronizada, el audio seleccionado comienza con las imágenes correspondientes; las zonas de logo sin contenido seleccionado quedan en silencio.

## Windows

El repositorio incluye `tools/build_windows.ps1`, que genera un ZIP con `Zucker Editor.exe` y sus recursos. El ZIP debe extraerse completo antes de abrir el EXE; no basta con copiar solo el ejecutable. El paquete incluye FFmpeg/FFprobe y sus avisos de distribución. La existencia del script no implica que haya un EXE publicado: cada entrega Windows debe comprobarse por versión y commit.

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

## Dirección musical y variaciones — 2.4.6

YouTube adapta cortes y movimientos a la energía suavizada por compás: Tranquilo (5–7 s), Animado (2,5–4 s) y Frenético (1–2 s en picos claros). Los finales y la cobertura pueden requerir duraciones distintas para conservar el tramo completo. Los zooms animados aceleran; paneos y zooms respetan el sujeto y los límites de encuadre. Si no hay evidencia del sujeto, se mantiene un movimiento central conservador. Planeta abre con zoom e inclinación continuos hasta el escenario 360 calibrado: hasta 3,2 s para completar el recorrido, separado al menos 40 s de otro planeta.

Hacer otro genera una semilla nueva: varían la colocación de cortes sobre la rejilla musical, los desempates de cámaras y los movimientos. La misma semilla conserva la reproducibilidad del render. Si solo hay una cámara o una vista válida, las alternativas quedan limitadas al material disponible. Las cuotas y la alternancia de músicos siguen activas.

Versiones: la antigua 2.1.35 equivale a 2.4.5; esta entrega es 2.5.1. Cada diez revisiones aumenta el número central: 2.4.9 → 2.5.0 → 2.5.1.

## Ubicaciones y espacio — 2.4.7

Al arrancar por primera vez se elige dónde guardar los vídeos importados, cachés, modelos de transcripción, temporales y logs. Al crear un proyecto nuevo se abre un selector nativo para elegir la carpeta de su sesión. Cancelar no crea un proyecto. Los proyectos siguen visibles desde las ubicaciones ya elegidas cuando sus discos están conectados. Si el disco elegido está desconectado, se solicita una ubicación: no se vuelve automáticamente al disco interno. Solo queda una pequeña preferencia de ubicación en la configuración del usuario.

Importar de nuevo el mismo contenido reutiliza el archivo ya importado, incluso si el nombre cambia. Los logs tienen rotación para limitar su crecimiento futuro. Cambiar de ubicación no migra por sí solo los proyectos antiguos; deben trasladarse con verificación y conservar sus entradas.

## Importación y guardado — 2.4.8

Suelta vídeos, audio, songs.json y un logo PNG/JPEG/WebP en el cuadro de archivos. Cada archivo aparece inmediatamente y muestra su transferencia real; después indica la comprobación del medio. El logo queda guardado para futuros proyectos y, si no hay uno personal, se utiliza el de Zucker.

Al pulsar Continue en la aplicación de escritorio, elige la carpeta del proyecto. El proyecto se guarda y aparece en Projects antes de pasar a las opciones de edición. Cancelar el selector conserva los archivos y mantiene la pantalla de entrada. Las importaciones del navegador se escriben directamente en el disco de trabajo, evitando una copia temporal completa adicional.

### Medley Populi y Highlights

En **Medley Populi**, añade vídeos de canciones diferentes, elige el audio de cada uno y fija la duración total en segundos. No exige un máster externo. El audio original es la opción inicial; si un vídeo no tiene pista de audio, ese extracto queda en silencio y se selecciona por calidad visual. Un audio externo asignado debe empezar en el mismo punto que su vídeo. La duración disponible depende de las fuentes: no se repite material para fabricar una duración mayor. Entre canciones se añade el negro y los fundidos de imagen y sonido configurados. Los extractos se buscan por cambios musicales; no se utiliza Whisper ni se sincronizan cámaras entre canciones.

YouTube puede activar **Detect vocal activity and possible instrumental solos**. Este análisis adicional separa voz, batería, bajo, guitarra y piano; guarda resultados para no repetirlo con el mismo audio e intervalo. La primera ejecución descarga el modelo en el disco de datos elegido. Los solos y rellenos son candidatos, especialmente el piano puede dar falsos positivos. Sin esta opción se usa el análisis rápido de ritmo e intensidad.

### Barras de carga — 2.5.1

Las barras generales y de tareas usan el progreso medido. Una tarea sin porcentaje disponible muestra actividad y lo indica; no se presenta como completada. Los renders nuevos empiezan desde cero. Medley muestra comprobación, highlights, extractos y ensamblado, sin etiquetas de sincronización de cámaras. Las consultas simultáneas idénticas de lectura se comparten mientras están en curso y el sondeo de estado no se solapa.

## Actualizaciones

Al abrir una versión que incluye el actualizador se comprueba la última release pública estable de `amehcgracia-byte/zucker_videos`. La consulta no bloquea el arranque; sin conexión se puede seguir trabajando. Una actualización requiere pulsar «Update and restart». No se instalan versiones anteriores ni se actualiza durante renders o revisión pendiente.

La descarga se guarda en Cache/Updates del disco seleccionado, se comprueba contra el SHA-256 publicado por GitHub y se prepara antes de cerrar el programa. macOS monta el DMG y verifica su firma; Windows extrae el ZIP completo, conserva la ruta del ejecutable y sus accesos directos. Un proceso externo sustituye el paquete y reabre la aplicación; si falla el reemplazo se restaura el anterior. El backup y la descarga se eliminan cuando arranca la versión nueva. La carpeta de instalación debe ser escribible por el usuario.

Las versiones anteriores sin actualizador necesitan una primera actualización manual. Para publicar una versión actualizable, la release debe contener un único DMG y un único ZIP Windows, con versión interna coincidente y digest SHA-256 en la API de GitHub. Los builds de Windows deben verificarse en Windows antes de publicarlos.

## Color y movimiento

La Sony es la primera referencia de color disponible; no se añade un aclarado global. La 360 se mide usando vistas del montaje y su subida de luminosidad es pequeña para proteger sombras y ruido. La corrección se mantiene fija por cámara durante el montaje. Si está activado el movimiento de cámaras fijas, las tomas no seleccionan una pausa estática; sin evidencia de un sujeto se usa un zoom central leve.
