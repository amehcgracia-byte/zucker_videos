# Exposición, movimiento y actualizaciones — 2.5.4

## Evidencia del proyecto Is cold outside

El export terminó correctamente. El aviso de «exposición potencialmente defectuosa» se generaba porque la Sony tenía una luminancia media 34,45 en una escena oscura. Además, YMIN/YMAX se usaban como indicio de clipping; eso no mide el porcentaje de píxeles recortados y confunde puntos luminosos/negros de conciertos con un defecto global.

La corrección anterior añadía 6 unidades al objetivo de todas las cámaras. En la 360, medida sobre la esfera completa, acababa en brightness=0,0808. Se eliminó ese aclarado general, la Sony conserva su imagen y el orden de referencia es Sony, Nikon, iPhone, 360, otras. Las vistas 360 usadas en el montaje se muestrean con su proyección; un perfil fijo por cámara evita cambios de exposición al entrar en cada corte. El muestreo reduce primero a dos frames/segundo y después reduce resolución para no proyectar fotogramas innecesarios.

La misma fuente y el mismo proyecto dieron luminancia 360 visible 18,22 y un ajuste nuevo limitado a 0,015. La diferencia entre la esfera completa y las vistas fue pequeña en este caso: el efecto dominante era el aclarado añadido y la intensidad de la corrección. Los balances de color y saturación también quedan acotados. Esto preserva el ambiente oscuro y reduce la amplificación del ruido; no recupera detalle que la cámara no haya grabado ni garantiza igualdad fotométrica entre encuadres de contenido diferente.

El plan tenía 18 tomas de teléfono con `full_static`, además de 39 zooms y 58 tomas Sony sin movimiento artificial. Cuando se activa movimiento de cámaras fijas, el plan ya no selecciona `full_static`. Si el sujeto es incierto, usa zoom central pequeño; las cámaras declaradas estáticas también se reconocen. Las tomas de una cámara handheld no pasan automáticamente a ser estáticas.

## Interfaz

- Render Frames arriba y abajo comparten el mismo envío y validación de tomas rechazadas.
- La cabecera de render muestra el nombre del proyecto/canción.
- Reabrir una revisión pendiente restaura también el nombre y los archivos antes de mostrarla.

## Actualizador

Consulta asíncrona de la última release pública estable del repositorio. No ofrece versiones menores/iguales, drafts ni prereleases. Si no hay conexión, la aplicación sigue funcionando sin modal de error.

La aceptación explícita descarga el instalador en el disco de datos elegido y comprueba tamaño y SHA-256 de la API de GitHub. Se prepara el paquete antes de cerrar la app. macOS monta el DMG, comprueba versión interna y firma, y prepara una copia en la carpeta de aplicaciones. Windows valida las rutas del ZIP y los recursos y versión del ejecutable; conserva el nombre de entrada actual para no romper accesos directos. Los paquetes nuevos usan `Zucker Editor.exe` estable.

Un helper externo espera al proceso que sale, conserva el paquete anterior, sustituye la aplicación y la reabre. Si falla el reemplazo/lanzamiento, restaura el anterior. Tras arrancar correctamente la versión nueva, se retiran su descarga y backup. No se cierran renders, revisión de tomas, composición, transcripción o tareas del motor para actualizar. Durante la preparación se bloquea iniciar trabajo nuevo. La API necesita token y origen local válido.

Limitaciones: instalación en una carpeta escribible; distribuciones anteriores sin actualizador necesitan una actualización manual inicial. El protocolo necesita releases con un único instalador compatible, metadata coincidente y digest publicado. Windows debe pasar su validación nativa en CI antes de publicar; las pruebas locales de macOS no acreditan un reemplazo Windows real.

Referencias del protocolo: https://docs.github.com/en/rest/releases/releases y https://docs.github.com/en/rest/releases/assets.

## Validación

120 pruebas locales pasaron; dos pruebas específicas del helper Windows se omiten en macOS y se han añadido al workflow de Windows. Las pruebas incluyen descarga dañada, no downgrade, ZIP inseguro/incompleto, consentimiento, trabajo activo, host no permitido, preparación de staging y sustitución/rollback del helper macOS. Se compararon imágenes antes/después de la fuente real 360 y se verificó el botón superior en el navegador. No se ha repetido el montaje completo de la canción para esta comparación.
