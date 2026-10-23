# Crear proyectos de Camtasia para las carreras (vertical)

Arrastra sobre uno de estos `.bat` la carpeta de la carrera (por ejemplo `Carreras\461-2026`).
Todos crean un proyecto **nuevo**: la plantilla nunca se modifica.

## Nuevo: foco-sol-6.1 (Camtasia 2026)

**Estado: apertura y exportación nativas comprobadas en la carrera 486.** Pasó la generación
individual/masiva y los chequeos del JSON. Se corrigió el aviso inicial que descartaba el banner
ganador y Camtasia exportó el MP4 completo a 1080×1920 con audio estéreo. Consulta
`ANALISIS-FOCO-SOL-6.1.md` para los resultados y revisa los tramos de baja confianza.

Arrastra una carrera, una reunión completa o varias carpetas sobre
**`Crear proyecto 2026 - foco-sol-6.1.bat`**. Para Valencia también hay un bat propio.
El mismo programa sirve para individual y masivo, recorre subcarpetas y continúa si alguna
carrera falla. Los proyectos llevan el sufijo `- foco-sol-6.1`, separados de tus ediciones.

Este es un **perfil nuevo de dirección de cámara**, con el detector YOLO existente;
no son pesos nuevos entrenados ni una llamada a un modelo de OpenAI.

- Busca el partidor mediante consenso de barras/puertas, en vez de centrar cualquier caballo
  visible en la previa. Si no lo reconoce, lo marca para revisión.
- Confirma el seguimiento dentro de cada toma. Reinicia las identidades y el sentido al cambiar
  de cámara, sin mezclar pistas de tomas diferentes.
- Vuelve a detectar con más detalle en partida, curva, recta y llegada. Conserva punteros
  parcialmente visibles en los bordes, que antes podían descartarse.
- Prioriza los punteros en tomas laterales. En perspectiva ambigua conserva el grupo: no
  inventa un orden de llegada a partir de la posición horizontal.
- No hace excursiones bruscas al rezagado. Incluye la mala partida si cabe con los punteros.
- Limita velocidad y aceleración, también después de la meta; no añade cortes de encuadre.
  Los cortes que ya trae la transmisión siguen presentes en el video fuente.
- Escribe los paneos completos en el JSON de Camtasia. No los comprime al último segundo,
  como ocurría con flash. La vista previa y el proyecto usan interpolación lineal equivalente.
- Añade un pulso discreto al banner ganador en la meta estimada. Se desactiva con
  `--sin-efecto-ganador`.

La primera ejecución tarda más que flash: añade rescate visual y, si está instalado, el
reconocimiento de voz. Las siguientes reutilizan el análisis. Los banners, cintillos y el
formato propio de cada hipódromo se siguen generando con el flujo existente.

### Narrador

El perfil usa **faster-whisper small/int8 local** cuando sus pesos están en
`modelos/faster-whisper-small`. No transmite el audio ni el video a un servicio externo.
La preparación se hace una sola vez con el Python de `.python-vision`:

```powershell
.\.python-vision\cpython-3.12.15-windows-x86_64-none\python.exe -m pip install -r requirements-sol.txt
.\.python-vision\cpython-3.12.15-windows-x86_64-none\python.exe preparar_narrador_sol.py
```

También acepta un `.srt` con el mismo nombre base del video, o un archivo
`<video>.narracion.json` con una lista de segmentos `start`, `end`, `text`.
Con `--transcripcion archivo.srt` se proporciona la transcripción de una carrera concreta.
En masivo pon cada transcripción junto a su video.

Se coordinan avisos de **partida, recta final y meta** con la imagen; las palabras conservan
sus tiempos. `--desfase-narrador 0.5` suma medio segundo a los avisos si la transmisión tiene
el relato adelantado. No modifica ni desplaza el audio del video.

**Límite actual:** reconocer un nombre en el relato no demuestra qué caballo corresponde a
una caja de detección. El sistema no identifica de forma garantizada nombres/dorsales/casacas,
ni el ganador por identidad. Los números de seguimiento son temporales y locales a la toma.
No cambia de caballo sólo porque el narrador mencione un nombre. La transcripción puede fallar
con nombres propios, música o gritos. La sincronía automática implementada es de fases.

### Uso y revisión

```powershell
python foco-sol-6.1.py "Carreras\La Rinconada\Reunión 41\C486"
python foco-sol-6.1.py "Carreras\La Rinconada\Reunión 41"
python foco-sol-6.1.py --pista valencia "Carreras\Valencia"
python foco-sol-6.1.py --narrador no --vista-previa "Carreras\La Rinconada\461-2026"
```

Opciones: `--salida`, `--nombre` (individual), `--recalcular` (rehace visión sol),
`--narrador auto|no|transcribir`, `--sin-recta-final` y `--sin-efecto-ganador`.
`transcribir` exige que el transcriptor esté preparado; `auto` usa sólo visión si falta.

Además del proyecto se guardan `... - foco.json` y `... - foco.revision.md`, con los intervalos
de baja confianza, fases, objetivos y decisiones. El informe no es una medición de exactitud:
revisa especialmente perspectivas frontales, oclusiones y cambios de toma. No se puede
prometer un foco correcto en todos los cuadros si la transmisión no muestra al puntero.

Los archivos `<video>.sol-6.1-cache.json` y `<video>.narracion-sol.json` se guardan al lado
del video. El primero valida archivo, pista, análisis de base y versión antes de reutilizarlo;
el segundo valida archivo y modificación. `--recalcular` rehace el primero. Si se sustituye
un SRT, se lee la versión actual en cada ejecución.

Pruebas: ejecutar `python -m unittest -v test_foco_sol` con el Python de visión.
El análisis de los problemas anteriores y los resultados de la comprobación están en
`ANALISIS-FOCO-SOL-6.1.md`.

| Bat | Qué hace |
| --- | --- |
| `Crear proyecto 2026.bat` | Banners, cintillo y video, con el foco que trae la plantilla. |
| `Crear proyecto 2026 - foco automatico.bat` | Igual, pero calcula el foco siguiendo a los caballos (paneo cuadro a cuadro) y crea `... - vista previa del foco.mp4` para revisarlo. |
| `Crear proyecto 2026 - foco flash.bat` | Versión express: calcula el foco mucho más rápido y deja **pocos movimientos de cámara** (paneos largos en vez de micro-zooms). No crea la vista previa. |
| `Crear proyecto 2026 - foco flash - sin recta final.bat` | Igual, pero **la recta final queda con el foco fijo** para armarla a mano (ver abajo). |
| `Crear proyecto 2026 - foco flash - Valencia.bat` | Igual, para las carreras de Valencia (ver abajo). |
| `Crear proyecto 2026 - foco flash - Valencia sin recta final.bat` | Valencia, con la recta final a mano. |
| `Crear proyecto 2026 - foco desde cero.bat` | Borra los movimientos de foco de la plantilla para empezar de cero. |

## Varias carreras de una vez

Cualquiera de estos bats acepta **una carpeta con varias carreras adentro**: entra en cada
subcarpeta (cada una con su video y sus banners), hace el proyecto de cada carrera y al final
muestra el resumen. También se puede llamar desde la terminal:

```
python camtasia_carrera.py --plantilla plantillas\plantilla-2026.tscproj ^
       --foco-auto --flash --sin-recta-final "D:\...\Carreras\La Rinconada\Reunion 40"
```

```
Carpeta con 8 carrera(s): D:\...\Reunion 40
 Carrera 1/8: 461-2026
 ...
 Resumen: 8 proyecto(s) hecho(s), 0 con problema(s)
   ok   461-2026                           461 - 2026.tscproj
```

Si una carrera falla (le falta el video, etc.), sigue con las demás y la marca en el resumen.

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

### Recta final a mano (`--sin-recta-final`)

Los bats `... sin recta final` usan ese modo: **en la recta final el foco no se mueve solo**.
Queda fijo en el encuadre donde arranca la recta, para que la armes a mano en Camtasia (ahí todavía
no hay una regla que sirva siempre: a veces conviene seguir al puntero y a veces al grupo). Después
de cruzar la meta vuelve el foco automático, con el ganador y un corte como el de la transmisión.

El bat dice hasta dónde llega lo automático y desde dónde queda a tu gusto:

```
Recta final desde 64.7 s; meta (arco/poste de llegada) en cuadro de 89.2 a 97.6 s.
Recta final de 64.7 a 97.6 s: sin foco automático (queda fijo en x=1020 para que lo ajustes a mano);
después de la meta sigue solo.
```

En el proyecto, ese tramo no tiene keyframes de cámara: agregás los tuyos en Camtasia y listo.

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
