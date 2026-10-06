# Cargas y comienzo por modo — 6 de octubre de 2026

## Fallos encontrados y correcciones

- Una tarea con porcentaje desconocido dejaba el relleno con anchura automática: parecía una barra al 100%. Hay un componente compartido para tareas, importación, solicitudes de red, Auto Read y la pantalla avanzada. El porcentaje conocido se limita a 0–100; el desconocido muestra actividad identificada como no medida, sin `aria-valuenow` falso.
- Los trabajos del wizard pueden compartir el identificador `current`. La barra podía conservar el 100% del render anterior. La identidad visual incluye fecha de inicio y proyecto, y se reinicia explícitamente al crear otro render.
- Medley acumulaba tareas desconocidas de fases ya terminadas. Solo mantiene su tarea activa y sus etapas se llaman comprobación, highlights, extractos y ensamblado.
- El sondeo cada segundo podía solaparse con una respuesta lenta. Ahora espera a la consulta en curso. Las lecturas de API idénticas simultáneas comparten la petición de red y entregan respuestas independientes; no hay caché persistente que oculte cambios.
- La apertura de Review podía repetirse cuando su estado era `review-loading`. Se protege también ese estado.
- Las pantallas de transición escondían las barras medidas durante 1,8 segundos. Se sustituyen por una notificación de fase, sin retrasar Review ni cubrir el progreso.
- La biblioteca de flyers solo se solicita al configurar Reel; Medley y Backstage no aplican el filtro temporal de canciones de YouTube.
- El borrador guarda el modo antes de los parámetros. Ya no se infiere por las entradas, ni se presenta toda la configuración de todos los modos.

## Recorrido

New Project → selector con el Einstein existente → archivos según modo → Continue → ubicación del proyecto en escritorio → proyecto guardado y visible → parámetros del modo → proceso habitual. El selector admite X, Escape y clic exterior. Si se cancela, el cuadro de archivos no se habilita hasta elegir un modo.

| Modo | Audio | Parámetros específicos |
|---|---|---|
| YouTube | Máster externo requerido | Trim, mezcla de cámaras, 360 si existe, highlights opcionales |
| Reel | Máster o audio propio de un único vídeo | Trim, duración, aspecto y mezcla de cámaras |
| 360 | Máster o audio propio de un único vídeo | Trim y configuración 360 cuando corresponde |
| Medley | Audio por vídeo; externo opcional; admite vídeos silenciosos | Duración total, negro, fundidos y asignación de audio |
| Backstage | Audio original de las cámaras | Duración documental y mensajes |

Medley sin pista de audio selecciona visualmente el extracto y genera silencio compatible con los demás clips. Un archivo externo elegido que no contenga audio produce un error explícito. El resultado señala cuáles fueron los extractos silenciosos. La unión sigue copiando los clips compatibles y se valida antes de publicarlos; no ejecuta Whisper ni la ingestión/sincronización multicámara.

## Verificación

- 48 pruebas específicas: importación, persistencia de los cinco modos, Medley con audio original y silencioso, negro/fundidos, progreso de fases, cachés y regresiones de movimiento/publicación atómica.
- Navegador: selector, New Jam vacío, reglas de los cinco modos, solo parámetros correspondientes, petición de ubicación antes de guardar, Medley completo de 4,5 segundos con un vídeo con audio y otro sin pista, sin máster externo.
- Barras de 400 px: 0%=0 px, 25%=100 px, 75%=300 px, 100%=400 px; desconocido=88 px en actividad, sin porcentaje declarado. Trabajo nuevo después de 100% volvió a 25% medido.
- Dos lecturas simultáneas de configuración provocaron una sola petición HTTP y ambas respuestas se pudieron leer.

Esto no acredita un porcentaje global de ahorro en todos los renders ni sustituye una comparación de fuentes equivalentes. Las verificaciones utilizan un servidor y proyectos aislados, sin modificar proyectos reales.
