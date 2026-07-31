# Traspaso — 360: saltos de encuadre entre cortes

## Estado actual

En `edit_plan.json` del proyecto real, con el código de `9363924`, los planos 360 individuales están prácticamente quietos: la retención sutil medida es de `0.75°/s`. El problema restante es la sucesión de cortes, no el movimiento dentro de cada plano.

Datos medidos:

- 44 cortes entre planos 360.
- Salto medio entre planos 360 consecutivos: `63°` de yaw.
- 11 saltos superan `100°`; el máximo es `159°`.
- Cada plano 360 dura aproximadamente `3.3 s`.

Conclusión: el flujo óptico intra-plano puede seguir siendo suave mientras el espectador percibe que la cámara gira, porque cada corte cambia bruscamente el encuadre local. El flujo óptico no mide este salto de framing entre cortes.

## Decisión pendiente con Chema

No implementar todavía: es una decisión de diseño y debe validarse visualmente con Chema.

Opciones:

- **A — Planos más largos:** aumentar los planos 360 de unos `3.3 s` a `8–12 s`, reduciendo el número de cortes.
- **B — Landmarks cercanos:** al seleccionar el siguiente landmark 360, priorizar el yaw más cercano al anterior para conservar variedad sin saltos grandes.
- **C — Más cámaras intercaladas:** aumentar la separación temporal entre planos 360 para que dos encuadres opuestos no aparezcan seguidos.

Recomendación inicial: combinar **A + B**, manteniendo variedad pero evitando saltos superiores a `90°`. Objetivo de validación: salto medio entre cortes 360 inferior a `45°`.

## Verificación propuesta

1. Medir el salto de yaw entre planos 360 consecutivos directamente desde el `edit_plan.json`.
2. Comparar media, máximo y número de saltos `>90°`.
3. Revisar visualmente una exportación con Chema y confirmar que la sucesión ya no se percibe como giro continuo.
4. Solo después de esa confirmación decidir si se implementa A, B o A+B.

