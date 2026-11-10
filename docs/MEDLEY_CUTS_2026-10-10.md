# Medley: puntos de corte y enlaces (10 de octubre de 2026)

## Diagnóstico

En la aplicación instalada 2.6.1 (8a7a992f), los fragmentos internos de una canción se concatenaban con cortes secos. Solo el primero y el último tenían fundidos. El selector puntuaba ventanas de segundos enteros por novedad musical y calidad visual, sin puntuar los extremos del corte. Un fragmento interesante podía empezar o terminar en mitad de un ataque.

## Cambio

- Disolución de vídeo de ocho frames (30 fps, aproximadamente 0,27 s) y mezcla de audio entre highlights de una misma canción.
- Conservación de los fundidos a negro/silencio y del espacio negro configurado entre canciones.
- Reserva de metraje adicional para compensar los solapes, sin acortar la duración solicitada ni repetir/congelar imágenes. Si la fuente está casi agotada, se reduce el número de highlights y se conserva un fragmento continuo.
- Ataques y pausas candidatos cada 50 ms, obtenidos de las muestras que el análisis ya lee. Los candidatos cercanos se agrupan; se puntúan la entrada y la salida, con un peso limitado que no puede rescatar un fragmento negro o silencioso.
- Un encode por canción, incluyendo la mezcla; el ensamblado de canciones conserva copia de streams. Misma resolución, CRF y configuración de audio.
- Cambia solo la versión de caché del análisis musical básico. Las cachés de imágenes y separación de instrumentos permanecen válidas.

Esto es una heurística de ataques/pausas y novedad, no un reconocimiento semántico de solos o frases completas. Falta validar la preferencia musical con el Medley del usuario; no se modifica ni se interrumpe su render en curso.

## Validación

`python -m pytest -q tests/test_medley.py`: 17 pasan (12,27 s).

Incluye exportaciones FFmpeg reales: solape rojo/azul en el punto interno (sin negro), señal de audio durante el solape, duración total, negros/silencio entre canciones, fuente sin audio y cancelación sin publicar. También cubre selección subsegundo de límites y detección de un ataque sintético. No se añadieron skip ni xfail.
