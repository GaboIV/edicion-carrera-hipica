# Crear proyectos de Camtasia para las carreras (vertical)

Arrastra sobre uno de estos `.bat` la carpeta de la carrera (por ejemplo `Carreras\461-2026`).
Todos crean un proyecto **nuevo**: la plantilla nunca se modifica.

| Bat | Qué hace |
| --- | --- |
| `Crear proyecto 2026.bat` | Banners, cintillo y video, con el foco que trae la plantilla. |
| `Crear proyecto 2026 - foco automatico.bat` | Igual, pero calcula el foco siguiendo a los caballos (paneo cuadro a cuadro) y crea `... - vista previa del foco.mp4` para revisarlo. |
| `Crear proyecto 2026 - foco flash.bat` | Versión express: calcula el foco mucho más rápido y deja **pocos movimientos de cámara** (paneos largos en vez de micro-zooms). No crea la vista previa. |
| `Crear proyecto 2026 - foco flash - Valencia.bat` | Igual, para las carreras de Valencia (ver abajo). |
| `Crear proyecto 2026 - foco desde cero.bat` | Borra los movimientos de foco de la plantilla para empezar de cero. |

Cada hipódromo usa su plantilla y su `--pista` (`rinconada` o `valencia`): la pista define dónde
está la franja que divide la pantalla y qué zonas del cuadro tienen gráficas del canal.

## Valencia

Las carreras de Valencia se transmiten distinto a las de La Rinconada, así que usan la plantilla
`plantillas/plantilla-2026-valencia.tscproj` (derivada de la otra) y el bat de Valencia:

- **Cintas de arriba**: 1080x240, pegadas al tope, tapando las gráficas del canal (el logo con la
  fecha y el recuadro de carrera/distancia/reunión). Van de la mano de los banners del usuario.
- **Cintillo**: 1080x100, sobre la franja que divide la pantalla, y sólo mientras la pantalla está
  partida en dos.
- **No hay tabla de posiciones**: no se recorta nada del video.
- El proyecto queda con tres pistas: el video, el cintillo y las cintas de arriba.

### Formato del video (4:3)

El canal de Valencia emite en 4:3, pero los archivos suelen venir **estirados a 16:9**. Antes de
empezar, el bat **valida el formato** del video que va a usar:

- si ya está en 4:3 (por ejemplo porque ya lo pasaste por el restaurador, o porque lo conseguiste
  así), lo usa tal cual: `Formato: el video ya está en 4:3 (1440x1080): no hay que corregirlo`;
- si viene estirado a 16:9, lo devuelve a 4:3 usando el restaurador del hipódromo
  (`restaurar_valencia.py`, el del bat "Restaurar video Valencia Pro", que además mejora imagen y
  audio) y sigue con esa copia `... - Enhanced Pro HD.mp4`;
- si el 4:3 viniera con barras negras a los costados, las recorta en vez de comprimir;
- si el restaurador no está, hace una compresión simple a 4:3 (`... - 4x3.mp4`).

El original nunca se toca, y la segunda corrida es instantánea: detecta el video 4:3 y no repite
nada.

El canal de Valencia **no muestra los tiempos parciales** en pantalla, así que no se pueden leer
del video: los parciales se **estiman por distancia**, repartiendo los metros de la carrera (los
que dicen los nombres de los banners: 400 m, 800 m, 1200 m, 1300 m) entre la partida y la meta, que
se detectan solos (los caballos aparecen al abrirse el partidor y en la llegada pasan por delante
de la cámara y se pierden unos segundos). El bat lo avisa al generar:

```
el canal no muestra los tiempos en pantalla; parciales estimados por distancia
(carrera de 1.6 a 84.8 s): 27.2 s, 52.8 s, 78.4 s, 84.8 s
Banners: sin tiempos 6.0 s, parcial 1 27.2 s, parcial 2 52.8 s, parcial 3 78.4 s, ganador 83.3 s
```

Si algún tiempo no cuadra, se ajusta a mano moviendo el clip del banner en Camtasia.

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

### Recta final y meta en el foco flash

El foco flash reconoce solo, en cada video, la recta final y la meta:

- **Recta final**: la toma larga desde la tribuna que termina en la llegada. Se reconoce porque la
  baranda blanca se va acostando sin saltos (de ~16° en la curva a horizontal en la meta). Si no se
  encuentra, la recta empieza en el penúltimo parcial, como antes.
- **En la recta** el foco sigue siempre al puntero, con su cabeza cerca del borde delantero, para que
  en el resto del cuadro entre la mayor parte posible del pelotón que viene detrás. Si el segundo
  viene tan lejos que no entraría igual, el puntero se va centrando de a poco.
- **Meta**: se busca el poste de llegada (franjas rojas y blancas, en el medio del arco). Apenas el
  puntero entra en el cuadro centrado en el poste, el foco se queda fijo en el arco hasta que la
  transmisión deja esa toma; después sigue con el ganador.

Esta búsqueda es una pasada extra, liviana, sobre el final del video (unos segundos); se guarda en el
mismo `*.foco-cache.json`. Al terminar, el bat muestra desde qué segundo detectó la recta y cuándo
estuvo la meta en cuadro; si dice que no encontró el poste, esa carrera usa el criterio anterior.

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
