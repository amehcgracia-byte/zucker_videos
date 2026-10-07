# Render detenido por carpeta temporal inexistente

Informe de Zucker Editor 2.5.2, commit `c904c4a`, proyecto All I got.

La excepción original no es un FFmpeg ausente. `tempfile.TemporaryFile()` intenta crear un registro bajo `Cache/temporary/9045`, pero esa carpeta ya no existe. El programa conserva la ruta temporal desde el arranque; el error de archivo se transforma incorrectamente en «ffmpeg is missing».

El registro completo muestra que el segmento 4 falló a las 18:50:43. El pool siguió ejecutando otros segmentos hasta las 19:03:53: al propagarse la excepción dentro del contexto del executor, su cierre esperaba a los trabajos pendientes. Esto explica la sensación de bloqueo después de terminar los segmentos que sí podían renderizarse. No se ha identificado qué borró la carpeta; no se atribuye a una limpieza concreta sin evidencia.

Corrección:

- Los registros del render 360 resuelven la ubicación de almacenamiento actual, crean su carpeta por proceso y usan un directorio explícito. Una eliminación entre creación y apertura se reintenta una vez. No hay fallback al disco del sistema cuando el disco elegido está desconectado.
- El primer fallo cancela los trabajos pendientes y avisa a los activos mediante el callback de progreso. Los recorridos FFmpeg y 360 liberan sus procesos al recibir esa excepción. Se conserva el primer error y su número de segmento.
- Un FileNotFoundError de archivos auxiliares ya no se etiqueta como un ejecutable FFmpeg ausente.

Validación: pruebas de carpeta eliminada, cambio de almacenamiento, eliminación concurrente, disco desconectado, diagnóstico y parada del pool. Dos renders 360 simultáneos sobre la fuente real `VID_20260831_220801_00_008.mp4`, con la carpeta temporal borrada previamente, completaron seis fotogramas cada uno y fueron verificados con FFprobe. Esta prueba corta no equivale a repetir el vídeo completo de 196 segmentos.

Resultados de regresión: 106 pruebas aprobadas y 11 fallidas. Las once se repitieron contra `core/stages/export.py` del commit instalado `c904c4a` y fallaron igualmente; corresponden a expectativas previas sobre proyección/movimiento 360. Las siete pruebas seleccionadas del arreglo pasan. La exportación completa sintética con cortes fraccionarios terminó y pasó su verificación.

La aplicación instalada sigue en 2.5.2: durante la validación el usuario inició otro render de All I got. No se ha interrumpido ni reinstalado ese proceso.
