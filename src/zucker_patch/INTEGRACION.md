# Zucker Editor - dynamic moves

Estos archivos han sido recreados en la rama fix/phase-0-input-readiness. El
planificador vive en core/dynamic_moves.py y ya está conectado al pipeline de
edición; esta guía documenta la integración y los límites de seguridad.

## Qué resuelve

- Selecciona aproximadamente uno de cada cuatro segmentos.
- Solo usa segmentos de 6 segundos o más.
- Nunca selecciona dos segmentos consecutivos.
- Genera movimientos lentos para clips planos: paneo, zoom y zoom + paneo.
- Genera curvas 360 con el mismo formato que core.camera_moves.
- Puede reutilizar la caché existente de MobileNet-SSD mediante
  load_cached_person_track().
- Nunca inicia una detección nueva durante la planificación.

## Ubicación

La aplicación importa la implementación desde core/dynamic_moves.py. El archivo
src/zucker_patch/dynamic_moves.py es un wrapper compatible y conserva el punto
de entrada de revisión/pruebas.

Ejecuta las pruebas desde la raíz:

    python3 -m pytest -q src/zucker_patch/test_dynamic_moves.py

## Integración por fases

### Integración actual: planificar después de crear segmentos

En core/stages/edit.py, después de construir todos los segmentos y antes de
escribir el artefacto final:

    from core.dynamic_moves import (
        build_dynamic_360_shot,
        generate_iphone_motion,
        plan_dynamic_moves,
    )

La regla de no consecutividad depende de la lista completa. La integración
recomendada es:

1. Generar el edit plan actual.
2. Llamar a plan_dynamic_moves(segments, seed=project fingerprint).
3. Para cada asignación, añadir movimiento solo si el segmento todavía no tiene
   motion ni una toma 360 grabada.
4. Para una fuente 360, crear segment["spherical_shot"] con
   build_dynamic_360_shot(...).
5. Para una fuente plana, añadir segment["motion"] =
   generate_iphone_motion(...).

No se debe aplicar el movimiento a clips inferiores a seis segundos, ni a
segmentos consecutivos, ni a tomas 360 dirigidas por el usuario.

### Movimiento suave del recorte iPhone

El exportador consume los campos nuevos de generate_iphone_motion e interpola:

    pan_x_start -> pan_x_end
    pan_y_start -> pan_y_end

con la misma expresión progress usada para el zoom y con easing smoothstep.
Si no existen los campos nuevos, conserva pan_x/pan_y como fallback. El rango
se limita a 0..1 y el zoom máximo sigue siendo 1.14.

### Fase C - 360

No hace falta crear otro renderizador. build_dynamic_360_shot() devuelve:

    type = recorded_move
    curve = [{"t": ..., "yaw": ..., "pitch": ..., "fov": ...}]

El camino existente sendcmd -> v360 ya consume esa curva. Antes de activarlo
hay que:

- respetar siempre las curvas grabadas del Director;
- aplicar el movimiento automático solo cuando no exista una curva dirigida;
- comprobar que la duración del segmento coincide con curve[-1]["t"];
- ejecutar validate_360_curve() durante las pruebas, no dentro de cada frame
  del export.

### Fase D - seguimiento de persona

El cache existente está en core/operator_avoidance.py y contiene:

    t, area_fraction, cx, cy
    subject_area_fraction, subject_cx, subject_cy

El planner solo usa subject_cx/subject_cy y nunca llama a MobileNet-SSD. Si
no hay sujeto cacheado, degrada a un zoom suave y no bloquea el pipeline. No
reutilizar la detección dominante como sujeto porque normalmente es el operador
de cámara.

### Screenshots 360 y timestamps

El endpoint /api/v1/wizard/spherical-preview acepta timestamp/time_sec,
timestamp_ratio y segment_id. El frontend envía el identificador de cada
landmark; si no hay un timestamp explícito, el servidor asigna un instante
estable y distinto por selección (ya no usa siempre el 35% del vídeo).

El frontend usa opcionalmente:

    data-spherical-timestamp
    data-frame-time
    data-timestamp
    data-spherical-segment

La selección que origine cada screenshot debe escribir el timestamp real del
frame elegido en data-spherical-timestamp. No usar un timestamp fijo ni el 35%
del vídeo. El timestamp y el identificador del segmento deben formar parte de
la identidad de caché, como ya hace el backend.

## Criterios de aceptación

1. Las pruebas nuevas pasan.
2. Un proyecto con 4 segmentos válidos produce como máximo 1 movimiento automático.
3. Ningún segmento de menos de 6 s recibe movimiento.
4. No hay dos movimientos contiguos.
5. Las tasas máximas de yaw/pitch/FOV quedan por debajo de los límites del módulo.
6. Las curvas 360 se reproducen por el renderizador existente.
7. La previsualización usa el mismo timestamp que la selección exportada.
8. Una curva del Director nunca se sobreescribe.

## Riesgos

- Activar automáticamente movimiento en todos los segmentos haría que el resultado
  se sintiera artificial; por eso la selección es limitada.
- La caché de MobileNet puede estar vacía en clips antiguos; el fallback debe
  seguir siendo estable.
- Cambiar el recorte iPhone requiere probar primero con 16:9 y 9:16.
- El cambio de screenshots debe validarse comparando timestamp, imagen de preview
  y segmento final, no solo mirando la URL.
