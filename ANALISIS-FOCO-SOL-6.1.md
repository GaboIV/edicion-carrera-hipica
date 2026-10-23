# Revisión de foco-sol-6.1

## Alcance revisado

Se revisaron los generadores Camtasia 8.6/2026, las dos plantillas 2026, los lanzadores
individuales/masivos, el análisis de foco, los cachés de carreras y el restaurador de Valencia.
La implementación nueva está enfocada en el JSON de Camtasia 2026 y reutiliza el montaje de
banners/cintillos/recortes. El restaurador y la exportación 8.6 mantienen su funcionamiento.

## Causas concretas encontradas

| Tramo o problema | Causa en el flujo anterior | Cambio |
| --- | --- | --- |
| Previa | Se centra el grupo de detecciones, incluso un caballo ajeno al partidor en el borde. | Búsqueda temporal del partidor; avisa si falta evidencia. |
| Mala partida | Excursión de 1.3 s al rezagado y velocidad hasta tres veces mayor. | Incluye al rezagado sólo cuando cabe junto al grupo; sin excursión impuesta. |
| Curva | Sentido heredado entre cámaras; ventana de orientación mezcla tomas. | Votación local por toma, seguimiento temporal y compensación del paneo del fondo. |
| Recta | Orden horizontal asumido incluso con perspectiva frontal; se descartan caballos pequeños y cortados. | Se conserva el borde y se rescatan detecciones; no se fuerza un orden en perspectiva ambigua. |
| Meta | Aparición del poste confundida con haber llegado; fijación prematura en un arco. | El poste ayuda a delimitar la toma, no inmoviliza el foco sin evidencia de cruce. |
| Después | Se elige el mayor caballo visible como supuesto ganador y se añaden cortes. | Continuidad de posición/identidad local, velocidad menor y ningún corte editorial añadido. |
| Movimiento | Flash simplifica el foco a paneos largos pero el escritor JSON los concentra en el último segundo. | Sol usa todo el intervalo del paneo lineal, con trayectoria equivalente a su vista previa. |
| Masivo | `a.salida` se modifica al procesar la primera carrera y se reutiliza en las siguientes. | Opciones independientes por carrera; descubrimiento recursivo y deduplicación. |
| Error silencioso | Si falla el análisis se conserva el foco de la plantilla. | Sol falla esa carrera explícitamente y continúa las demás; devuelve estado de error. |

## Diseño y límites

El nombre `foco-sol-6.1` identifica un algoritmo/perfil nuevo; no es un modelo neuronal
entrenado con carreras etiquetadas. YOLO sigue detectando personas/caballos. El seguimiento
combina posición, apariencia aproximada y movimiento del fondo dentro de una toma. Las
identidades se reinician al cambiar de cámara y pueden cambiar con oclusiones prolongadas.

La prioridad es conservar el cuerpo de los punteros cuando la orientación es fiable. El
encuadre mantiene márgenes, una zona de reposo y límites de movimiento. Si la nueva toma
coloca al líder lejos del encuadre anterior, no siempre es posible conservarlo y efectuar un
movimiento lento simultáneamente; se marca ese tramo en el informe de revisión.

La narración se procesa localmente con marcas de palabra. Las señales se aceptan en ventanas
plausibles alrededor de partida/recta/meta. No se utiliza una mención aislada para identificar
un caballo: haría falta reconocimiento de dorsal/casaca y un padrón de participantes validado.
El reconocimiento de fases y el reconocimiento de identidad son problemas diferentes.

El efecto de victoria es un pulso de 1.8% del banner ganador, con parámetros de escala nativos.
Ocurre en la meta estimada y es opcional. No altera el audio, el video original ni el orden de
los banners. Los saltos de cámara incluidos por la transmisión no se eliminan al mover el foco.

## Fuentes técnicas consultadas

- [Ultralytics: seguimiento de objetos](https://docs.ultralytics.com/modes/track): persistencia
  e importancia del movimiento de cámara. Se implementa una asociación ligera sobre los
  detecciones y cachés locales; no se cambia de tracker ni se reentrena YOLO.
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper): inferencia local en CPU/int8,
  transcripción con tiempos de palabra y VAD. Se decodifica audio con ffmpeg para evitar una
  incompatibilidad observada entre el lector del paquete y PyAV 19.

## Validación

Las pruebas automatizadas cubren conservación de caballos en el borde, exclusión de la parte
del narrador en pantalla dividida, aislamiento de la orientación entre tomas, asociación con
movimiento de cámara, ausencia de cortes añadidos, límites del encuadre y velocidad tras meta,
cuadros sin detección, análisis vacío, tiempos de palabra, SRT y agrupación de tramos de revisión.
Resultados de esta sesión:

- **15 pruebas automatizadas correctas**, incluidos aislamiento de salidas en masivo,
  deduplicación de carpetas anidadas y continuación después de una carrera fallida.
- **Generación individual real** de la carrera 486 de La Rinconada, con transcripción local.
  Se reconoció el aviso de recta final a los 68.12 s; el parcial usado por el flujo anterior
  situaba esa fase a los 85.2 s. Se comprobó visualmente el partidor y se ajustó la perspectiva
  de la curva después de inspeccionar el resultado, no sólo las detecciones.
- **Generación masiva real de dos carreras**, 486 de La Rinconada y 203 de Valencia, con
  selección independiente de plantillas y sin fallas de generación. Esta prueba masiva se
  hizo sin transcriptor; el reconocimiento de voz se probó en la ejecución individual 486.
- Comprobación independiente de medios, IDs, duraciones y orden de animaciones. La diferencia
  máxima entre el encuadre del JSON y la interpolación de la vista previa fue inferior a
  6 píxeles del video normalizado, por cuantización a fotogramas.
- Vista previa completa de la 486, con audio estéreo, a 608×1080 y 30000/1001 fps. Es una vista
  del encuadre calculado; no incluye el montaje de banners/cintillo de Camtasia.
- **Apertura nativa corregida y comprobada.** Tras recuperar el control de Camtasia se guardó
  una copia desde el propio editor y se comparó con el proyecto generado. El aviso inicial
  correspondía al banner ganador: sus dos cambios de escala estaban agrupados en un único
  tramo visual y Camtasia eliminaba ese clip. Se corrigió la serialización para escribir dos
  animaciones separadas. La nueva copia abrió sin el aviso y conservó el banner ganador.
  Se añadió una prueba de regresión y una validación previa al guardado para este caso.
- Se inspeccionaron en el editor el partidor, la entrada a la recta (aprox. 74.8 s), la recta
  final (87.7 s) y la llegada (99.2 s), con banners y recorte de posiciones.
- **Exportación nativa completada.** Camtasia confirmó «Los medios se han exportado
  correctamente». El MP4 es de 1080×1920, 30 fps, 149.367 s de video y 149.397 s de audio
  estéreo. La decodificación completa de imagen y audio con ffmpeg terminó sin errores.
  Se inspeccionaron fotogramas del archivo exportado en 0, 12, 35, 74.8, 87.7, 96, 99.2 y
  120 s; conserva los banners, el cintillo, el recorte de posiciones y el banner ganador.
  El archivo está en `validacion-sol/486 - 2026 - foco-sol-6.1.mp4`, y el proyecto comprobado
  en `validacion-sol/486-revision2/`. Camtasia quedó abierto con ese proyecto.

La validación técnica de una carrera no garantiza identificación correcta de los punteros en
todas las transmisiones. Los tramos de baja confianza siguen necesitando revisión visual.
Los proyectos de prueba y la vista previa están en `validacion-sol/` y quedan excluidos de Git.

No se asignan notas 1–10 sin una evaluación visual sobre carreras variadas y una referencia
etiquetada. Más detecciones o menos cortes no demuestran por sí solos una mayor precisión.
