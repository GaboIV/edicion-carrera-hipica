# Crear proyectos de Camtasia para las carreras (vertical)

Arrastra sobre uno de estos `.bat` la carpeta de la carrera (por ejemplo `Carreras\461-2026`).
Todos crean un proyecto **nuevo**: la plantilla nunca se modifica.

| Bat | Qué hace |
| --- | --- |
| `Crear proyecto 2026.bat` | Banners, cintillo y video, con el foco que trae la plantilla. |
| `Crear proyecto 2026 - foco automatico.bat` | Igual, pero calcula el foco siguiendo a los caballos (paneo cuadro a cuadro) y crea `... - vista previa del foco.mp4` para revisarlo. |
| `Crear proyecto 2026 - foco flash.bat` | Versión express: calcula el foco mucho más rápido y deja **pocos movimientos de cámara** (paneos largos en vez de micro-zooms). No crea la vista previa. |
| `Crear proyecto 2026 - foco desde cero.bat` | Borra los movimientos de foco de la plantilla para empezar de cero. |

## Cintillo

El cintillo aparece **solo mientras la pantalla está dividida en dos** (cuando abajo se ve al
narrador) y desaparece en cuanto el video vuelve a pantalla completa. Los tramos se detectan en
el video por la franja negra "SEXTA CARRERA ... HIPÓDROMO" y cada tramo lleva su fundido de
entrada y de salida. Si un tramo dura más que el gif, se cubre con varios clips pegados, porque
Camtasia no repite los gif solos y un clip nunca puede pedir más material del que tiene el archivo.

## Recorte de las posiciones (pista 2)

El clip recortado que amplía la lista de posiciones aparece **solo mientras esa lista está en
pantalla**: se detecta por el verde oscuro del recuadro y el texto blanco de los nombres. Cuando el
canal la saca, el recorte desaparece.

## Foco en la recta final

Con cámara de frente o de atrás y los caballos juntos se centra el grupo, porque la dirección no
dice quién va adelante. Pero si uno se despegó se lo sigue igual: en la recta final el encuadre se
queda con el puntero en vez de irse con los de atrás.

## Archivos que se guardan al lado del video

Se calculan una sola vez y después se reutilizan (borralos para forzar que se analice de nuevo):

- `*.foco-cache.json` — análisis del video (detecciones, cortes, pantalla dividida). Lo hace el
  Python de visión (`.python-vision`, con YOLO).
- `*.carrera-cache.json` — parciales (400m, 800m, ...), partida, pantalla dividida y lista de
  posiciones.

El perfil flash aprovecha el análisis guardado por el foco automático normal. Al revés no: si el
análisis guardado es el flash, el foco automático normal lo rehace.

## Requisitos

`ffmpeg`/`ffprobe` en el PATH y el Python de visión en `.python-vision` (ya viene con el repo).
