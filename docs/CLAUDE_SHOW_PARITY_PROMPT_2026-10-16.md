# Prompt de traspaso para Claude

Continúa desde la rama `codex/show-preview-parity`, basada en tu commit `77271072`, no desde una reconstrucción antigua de la maqueta. Lee `docs/SHOW_RENDER_PARITY_2026-10-16.md` y `docs/CLAUDE_HANDOFF_SHOW_CURRENT_2026-10-16.md`. Integra el commit de esta revisión sin sobrescribir las decisiones ni los materiales privados del proyecto.

El usuario pide una sola aplicación coherente. No todo debe parecer un dibujo animado: dibujos torpes y manuales para algunas bromas, fórmulas serias y abundantes para pensar, fotografías originales para personajes recortados. No vuelvas a generar dibujos en Hugging Face/DreamShaper durante el render para suplir imágenes no enlazadas. Adjunta el PNG realmente elegido a `params.picture`, conserva su hash y verifica que preview y export usan el mismo archivo. No proclames canónica una guitarra solo por su nombre ni una imagen solo por estar en una carpeta llamada approved.

Esta revisión elimina renders automáticos al abrir/editar, impide sustituciones automáticas del arte en el recorrido revisado, cambia las fórmulas/énfasis y conecta recortes con fondos estáticos al exportador. Hay tests y un MP4 sintético real de comprobación. No reactives el prefetch de todas las escenas para disimular latencia: las imágenes elegidas se editan sobre el vídeo en vivo; Preview with effects es explícito.

La plastilina no faltaba por el prompt: el proyecto Pilot inspeccionado no tiene `show_studio/clay.json`. Los archivos públicos aprobados están en `assets/clay/simon/`; el ejemplo privado importado está en Application Review. Importa materiales Y decisiones, no solo código. Los tiempos originales 223.62–244.90 no valen automáticamente en el vídeo recuperado ya recortado. Alínea las palabras primero y comprueba full/bubble/pip/blend, sus retornos al presentador y poses quietas entre frases. El protagonista es el de Simón: gorra verde ladeada con visera saliente, abrigo amarillo; no el antiguo muñeco barbudo.

No digas que todos los efectos son live: la vista inmediata del storyboard todavía no compone matemáticas, vapor, personajes con voz, recorte o plastilina completos. Queda compartir ese compositor; mientras tanto indica con claridad qué necesita Preview with effects. El nuevo soporte de recorte exporta polígonos/mattes guardados sobre imágenes; no es segmentación automática general ni soporta ya fondos de vídeo. No ocultes estados unsupported/failed.

El usuario preparará dos tomas nuevas. Conserva los originales y sincronízalos; no vuelvas a editar una salida que ya lleva subtítulos/efectos como si fuese cámara limpia. Usa las fotos nítidas existentes de la nevera/planta como recursos, sin recrear a esos personajes. Las grabaciones, voces y fotos privadas se quedan fuera de GitHub público.

Antes de instalar: ejecuta las pruebas dirigidas del informe, recorre el storyboard en la app real, verifica que arrastrar una imagen no hace POST de preview ni inicia FFmpeg, exporta un fragmento con la misma imagen, el recorte/fondo y la plastilina correctamente alineada. Comprueba contenido/commit del paquete. Entrega qué funciona y qué falta sin prometer que toda la aplicación está validada.

## Novedad comprobada después del PR: dc285d2e

La rama local `claude/show-app-build` avanzó a `dc285d2e` después de nuestra base `77271072`. Ese commit todavía no está integrado en este PR. No apliques ambos cambios conservando automáticamente «la última versión» de los archivos compartidos.

- Sus mejoras de densidad de propuestas, generación explícita con Claude y codificación hardware son trabajo nuevo; no deben desaparecer al integrar el PR.
- Conflicto de flujo: `_drawn_later` muestra una tarjeta en lugar del dibujo hasta aceptar, y el endpoint de aceptar inicia su generación. El usuario quiere ver el resultado real ANTES de aprobar. Separa crear propuesta, generar borrador, revisar resultado y aprobar; aceptar una tarjeta no equivale a aprobar el PNG que aún no existe.
- Conflicto de identidad: `claude_picture` guarda su PNG en una caché, pero esta ruta no lo vincula a `params.picture` como resultado elegido. Guarda el resultado en la biblioteca del proyecto y enlázalo explícitamente; preview/final no deben volver a decidir el motor ni recrear la imagen.
- Conflicto de concurrencia: su nuevo pool se lanza antes del bucle de `make_items`. Al integrar `allow_generation=False`, protege TAMBIÉN la creación/envío de ese pool, no solo la rama del bucle. Un render revisado sin permiso de generación debe iniciar cero llamadas al proveedor, aunque Claude esté instalado. Añade una prueba con motor Claude, propuesta sin PNG y proveedor que falle si es invocado.
- `_encoded` captura cualquier Exception y repite con CPU. Limita el fallback a fallos del codificador; un error de dibujo, archivo o receta no debe repetir toda la composición bajo el mensaje de error hardware. Conserva cancelación y publicación atómica.
- «Cinco veces más rápido» no está verificado por esta revisión: el commit describe una medición anterior de 1 h 55 min, pero necesitamos el render comparable después, calidad visual y fase por fase para validar esa mejora.

La revisión de dc285d2e ha sido estática, no una ejecución del nuevo motor ni una medición de su rendimiento. No se ha alterado el checkout activo de Claude ni la app instalada.
