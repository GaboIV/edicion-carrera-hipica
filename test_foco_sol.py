"""Pruebas de decisiones que antes causaban pérdida del puntero o saltos."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
import sys
import camtasia_carrera as generador

from foco_sol_6_1 import (cajas_utiles, direccion_toma, planificar, Seguimiento,
                         VENTANA, intervalos)
from narracion_sol import eventos_narrador, leer_transcripcion


def row(t,boxes,orient=True):
    # Jinetes adelantados a la derecha: dirección conocida en toma lateral.
    riders=[[c[0]+.65*(c[2]-c[0]),c[1]-30,c[0]+.9*(c[2]-c[0]),c[1]+30] for c in boxes] if orient else []
    return {"t":t,"caballos":boxes,"jinetes":riders,"apariencias":[[0]*12 for _ in boxes],
            "movimiento":None,"dividida":False,"coherencia":1,"partidor":None}


def data(duration=20):
    return {"ancho":1920,"duracion":duration,"fps":30,"mad":[0]*int(duration*30),
            "muestras":[{"t":0,"hist":1,"banda":0},{"t":duration,"hist":1,"banda":0}]}


class FocusTests(unittest.TestCase):
    def test_edge_leader_is_not_discarded(self):
        horses,_=cajas_utiles([[1850,400,1920,470,.8,17],[1500,400,1640,470,.9,17]],False,"rinconada",1920)
        self.assertEqual(len(horses),2)

    def test_narrator_excluded_when_split(self):
        horses,_=cajas_utiles([[800,700,1000,900,.9,17],[800,300,1000,420,.9,17]],True,"rinconada",1920)
        self.assertEqual(len(horses),1)

    def test_direction_does_not_leak_between_shots(self):
        boxes=[[700,300,900,400],[1000,300,1200,400]]
        rows=[row(i*.25,boxes,i<8) for i in range(16)]
        direccion_toma(rows,[2])
        self.assertTrue(all(r["sentido"]==1 for r in rows[:8]))
        self.assertTrue(all(r["sentido"]==0 for r in rows[8:]))

    def test_tracking_compensates_camera_motion(self):
        tracker=Seguimiento()
        a=tracker.actualizar(row(0,[[900,300,1100,400]]))[0]
        r=row(.25,[[700,300,900,400]]);r["movimiento"]=[-200,0]
        b=tracker.actualizar(r)[0]
        self.assertEqual(a["id"],b["id"])

    def test_no_synthetic_camera_cuts(self):
        rows=[row(i*.25,[[500 if i<20 else 1400,300,700 if i<20 else 1600,400]]) for i in range(80)]
        result=planificar(rows,data(),[5,10,15],0)
        self.assertEqual(result["cortes"],[])
        self.assertTrue(all(k[2]!="corte" for k in result["keyframes"]))
        for a,b in zip(result["keyframes"],result["keyframes"][1:]):
            self.assertLessEqual(abs(b[1]-a[1])/(b[0]-a[0]),361)

    def test_after_finish_speed_limit(self):
        rows=[row(i*.25,[[500 if i<65 else 1400,300,700 if i<65 else 1600,400]]) for i in range(80)]
        result=planificar(rows,data(),[5,10,14],0)
        points=result["seguimiento"]
        for a,b in zip(points,points[1:]):
            if a["fase"]==b["fase"]=="despues":
                self.assertLessEqual(abs(b["x"]-a["x"])/.25,230.1)

    def test_blank_frames_hold_focus(self):
        rows=[row(i*.25,[]) for i in range(8)]
        result=planificar(rows,data(2),[],0)
        self.assertEqual(len(set(k[1] for k in result["keyframes"])),1)

    def test_no_out_of_bounds_focus(self):
        rows=[row(i*.25,[[1800,300,1920,400]]) for i in range(80)]
        result=planificar(rows,data(),[5,10,15],0)
        self.assertTrue(all(VENTANA/2-.1<=k[1]<=1920-VENTANA/2+.1 for k in result["keyframes"]))

    def test_empty_analysis_fails_clearly(self):
        with self.assertRaisesRegex(ValueError,"muestras"):planificar([],data(),[],0)

    def test_word_timestamp_not_segment_start(self):
        segment={"start":50,"end":70,"text":"Ahora entran en la recta final",
                 "words":[{"word":"recta","start":65,"end":66},{"word":"final","start":66,"end":67}]}
        events=eventos_narrador([segment],.5)
        self.assertEqual(events[0]["t"],65.5)

    def test_srt_multiline_and_accents(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"voz.srt"
            path.write_text("1\n00:01:05,500 --> 00:01:07,000\nEntraron en la\nrecta final\n\n",encoding="utf-8")
            s=leer_transcripcion(path)
            self.assertEqual(s[0]["start"],65.5)
            self.assertEqual(eventos_narrador(s)[0]["tipo"],"recta")

    def test_merged_review_intervals(self):
        self.assertEqual(intervalos([0,.25,.5,.75,1,3],.26,1),[[0,1]])

    def test_native_pulse_reversal_needs_two_visual_animations(self):
        # Caso real que Camtasia descartaba al abrir: la escala subía y bajaba
        # dentro de una sola animación visual de 0.8 s.
        clip={"parameters":{"scale0":{"keyframes":[
            {"time":1500,"endTime":1800,"duration":300,"value":1.018},
            {"time":1800,"endTime":2300,"duration":500,"value":1.0}]}},
            "animationTracks":{"visual":[{"endTime":2300,"duration":800}]}}
        self.assertTrue(generador.problemas_animaciones_visuales(clip))
        clip["animationTracks"]["visual"]=[{"endTime":1800,"duration":300},{"endTime":2300,"duration":500}]
        self.assertEqual(generador.problemas_animaciones_visuales(clip),[])

    def test_batch_nested_deduplicated_and_independent_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ("reunion/C1","reunion/C2"):
                race=root/name;race.mkdir(parents=True);(race/"carrera.mp4").write_bytes(b"video")
            called=[]
            def process(race,args,template):
                called.append((race,args.salida,args.pista))
                args.salida=race
                return race,False
            argv=["camtasia_carrera.py","--sol-6-1","--plantilla","plantillas/plantilla-2026.tscproj",
                  str(root),str(root/"reunion/C1")]
            with patch.object(sys,"argv",argv),patch.object(generador,"procesar_carrera",process):
                generador.main()
            self.assertEqual(len(called),2)
            self.assertTrue(all(c[1] is None for c in called))

    def test_batch_failure_produces_nonzero_exit_and_continues(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ("C1","C2"):
                race=root/name;race.mkdir();(race/"carrera.mp4").write_bytes(b"video")
            called=[]
            def process(race,args,template):
                called.append(race)
                if len(called)==1:raise RuntimeError("carrera inválida")
                return race,False
            argv=["camtasia_carrera.py","--sol-6-1","--plantilla","plantillas/plantilla-2026.tscproj",str(root)]
            with patch.object(sys,"argv",argv),patch.object(generador,"procesar_carrera",process):
                with self.assertRaises(SystemExit) as error:generador.main()
            self.assertEqual(error.exception.code,1)
            self.assertEqual(len(called),2)


if __name__=="__main__":unittest.main()
