# Tutorial de bienvenida con Einstein

Al abrir la interfaz sin una respuesta guardada, se pregunta exactamente: «¿Quieres aprender lo que puede hacer Sugar Mixer?». El texto del recorrido identifica las herramientas de Zucker Editor. Sí y No se guardan antes de continuar; cerrar la pregunta con X o Escape equivale a No. Si no se puede guardar, se muestra el error y se permite reintentar.

El estado está en `~/.config/ZuckerEditor/tutorial.json`, junto a las preferencias de almacenamiento, independiente del puerto del servidor, el proyecto y las actualizaciones. Un perfil nuevo recibe la pregunta; conservar el perfil al reinstalar conserva también la respuesta. No se reinicia la preferencia con cada versión. Ayuda → Tutorial con Einstein permite repetir el recorrido voluntariamente.

Veinte explicaciones cubren proyectos, modalidades, importación, guardado, recorte de audio, preparación en segundo plano, highlights, cámaras, 360, ajustes finos, Reel, Backstage, Medley, render, informes, revisión, subtítulos, superposiciones y resultado. La ilustración original aportada por el usuario se incluye sin modificar.

El fondo se oscurece y un foco ovalado señala el control visible. Los controles de pantallas aún no disponibles se muestran como vistas de demostración inertes, sin identificadores duplicados, manejadores ni fuentes multimedia activas. El tutorial no modifica parámetros, no navega el proyecto ni inicia análisis. Los bocadillos grandes incluyen Anterior, Siguiente y Terminar, con X/Escape para salir. Hay adaptación a pantalla estrecha, foco de teclado, diálogo modal y respeto por movimiento reducido. Un aviso de actualización recibido durante el tutorial espera a su cierre.

Validación: pruebas de ambas respuestas persistentes entre instancias, rechazo de valores inválidos y pruebas de menú. Recorrido manual de los 20 pasos en servidor de prueba aislado, cierre y recarga sin repetición, revisión visual en pantalla estrecha y a 1200×820. El render de la aplicación instalada no se ha consultado ni interrumpido. Pendiente empaquetar e instalar junto con los nuevos menús.
