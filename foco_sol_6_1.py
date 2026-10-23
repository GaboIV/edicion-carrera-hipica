# -*- coding: utf-8 -*-
"""Director de encuadre local foco-sol-6.1. Coordenadas normalizadas a alto 1080.

YOLO sigue siendo el detector, no se presenta este perfil como un modelo entrenado.
La narración aporta eventos de carrera; no permite identificar una caja por un nombre.
"""
import argparse
import bisect
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

import foco_carrera as base
from narracion_sol import narracion, eventos_narrador

VERSION = 1
PERFIL = "foco-sol-6.1"
FPS_ANALISIS = 4
VENTANA = base.VENTANA


def guardar(path, datos):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporal = path.with_name(path.name + ".tmp")
    temporal.write_text(json.dumps(datos, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    temporal.replace(path)


def cajas_utiles(cajas, dividida, pista, ancho):
    """Conserva punteros parcialmente visibles: el borde no es un motivo para descartarlos."""
    cab, jin = [], []
    for x1, y1, x2, y2, conf, clase in cajas:
        cx, cy = (x1+x2)/2, (y1+y2)/2
        if conf < .20 or y2-y1 < 15 or x2-x1 < 9:
            continue
        if dividida and cy > base.PISTAS[pista]["corte"]:
            continue
        if any(a <= cx <= c and b <= cy <= e for a,b,c,e in base.PISTAS[pista]["graficas"]):
            continue
        (cab if clase == base.CABALLO else jin).append([x1,y1,x2,y2])
    # Elimina duplicados, no caballos pequeños por estar más lejos de la cámara.
    cab = [c for i,c in enumerate(cab) if not any(iou(c,o) > .75 for o in cab[:i])]
    return cab, jin


def iou(a, b):
    inter = max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    return inter / max(1,(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter)


def centro(c):
    return np.array([(c[0]+c[2])/2, (c[1]+c[3])/2], dtype=float)


def partidor(f):
    """Busca la fila de puertas por sus barras verticales repetidas, no el caballo del borde."""
    gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 60, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi/180, 28, minLineLength=28, maxLineGap=5)
    if lines is None:
        return None
    h,w = gray.shape
    # Puertas blancas repetidas: la morfología tolera barras cortas, banderas y
    # la perspectiva diagonal que fragmenta las líneas del partidor.
    hsv=cv2.cvtColor(f,cv2.COLOR_BGR2HSV)
    white=((hsv[:,:,1]<65)&(hsv[:,:,2]>165)).astype(np.uint8)*255
    white[:int(h*.14)]=0;white[int(h*.60):]=0
    vertical=cv2.morphologyEx(white,cv2.MORPH_OPEN,np.ones((11,1),np.uint8))
    doors=cv2.morphologyEx(vertical,cv2.MORPH_CLOSE,np.ones((5,26),np.uint8))
    candidates=[]
    for contour in cv2.findContours(doors,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
        x,y,cw,ch=cv2.boundingRect(contour)
        fill=cv2.contourArea(contour)/max(cw*ch,1)
        if .20*w<cw<.63*w and .10*h<ch<.50*h and cw/ch>1.5 and fill>.08:
            candidates.append((cv2.contourArea(contour),(x+cw/2)*2))
    if candidates:
        score,cx=max(candidates)
        return [round(cx,1),round(score/100,2)]
    bars = [l for l in lines.reshape(-1,4) if abs(l[0]-l[2]) < 6 and
            .12*h < (l[1]+l[3])/2 < .60*h and abs(l[1]-l[3]) < .4*h]
    best = None
    for line in bars:
        y = (line[1]+line[3])/2
        group = [l for l in bars if abs((l[1]+l[3])/2-y) < 18]
        xs = sorted(set(int(l[0]//5)*5 for l in group))
        # Ventanas de ancho razonable evitan unir barandas de todo el hipódromo.
        for x in xs:
            nearby = [u for u in xs if x <= u <= x+.50*w]
            if len(nearby) < 7 or nearby[-1]-nearby[0] < .13*w:
                continue
            gap = np.diff(nearby)
            regular = float(np.mean((gap >= 5) & (gap <= 35)))
            score = len(nearby)*regular
            if best is None or score > best[0]:
                best = (score, (nearby[0]+nearby[-1])/2, y)
    return [round(best[1]*2,1), round(best[0],2)] if best else None


def apariencia(f, c):
    x1,y1,x2,y2 = [int(v/2) for v in c]
    # Parte superior del caballo: jinete/casaca, sin suelo.
    roi = f[max(y1,0):max(y1+1,int(y1+.5*(y2-y1))), max(0,x1):max(x1+1,x2)]
    if not roi.size:
        return [0.0]*12
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv],[0],cv2.inRange(hsv,(0,65,45),(180,255,255)),[12],[0,180]).ravel()
    hist /= max(float(hist.sum()),1)
    return [round(float(x),3) for x in hist]


def analizar_visual(video, d, partida, final, meta, pista, recalcular=False):
    """Una decodificación reducida; rescata detalle sólo en partida, curva y llegada."""
    ancho = d["ancho"]
    path = Path(video).with_suffix(".sol-6.1-cache.json")
    firma = {"archivo":base.identidad(video), "version":VERSION, "pista":pista,
             "partida":round(partida,2), "final":round(final,2), "meta":round(meta,2),
             "base":hashlib.sha256(json.dumps(d["detecciones"],sort_keys=True).encode()).hexdigest()}
    if path.exists() and not recalcular:
        try:
            cached=json.loads(path.read_text(encoding="utf-8"))
            if cached.get("firma")==firma:
                if cached.get("version_partidor")!=2:
                    actualizar_partidor(video,cached["muestras"],partida,ancho)
                    cached["version_partidor"]=2
                    guardar(path,cached)
                if cached.get("version_rescate")!=2:
                    refinar_partida(video,cached["muestras"],partida,ancho,pista)
                    cached["version_rescate"]=2
                    guardar(path,cached)
                print("  reutilizando visión sol-6.1",flush=True)
                return cached["muestras"]
        except (ValueError,OSError):
            pass
    from ultralytics import YOLO
    detector = YOLO(str(Path(base.AQUI)/"modelos"/base.MODELO))
    times=np.array([x["t"] for x in d["detecciones"]])
    td,bd=base.pantalla_dividida(d["muestras"])
    wa,ha=ancho//2,540
    cmd=["ffmpeg","-v","error","-i",video,"-vf",f"fps={FPS_ANALISIS},scale={wa}:{ha}",
         "-f","rawvideo","-pix_fmt","bgr24","-"]
    proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    out=[]; prev=None; prev_cab=[]; i=0; pending=[]

    def completar():
        if not pending:
            return
        results=detector.predict([f for _,f in pending],imgsz=1280,conf=.18,
                                 classes=[0,17],verbose=False)
        for (idx,f),r in zip(pending,results):
            boxes=[[round(v*2,1) for v in xy]+[round(conf,3),int(cls)]
                   for xy,conf,cls in zip(r.boxes.xyxy.tolist(),r.boxes.conf.tolist(),r.boxes.cls.tolist())]
            row=out[idx]
            row["caballos"],row["jinetes"]=cajas_utiles(boxes,row["dividida"],pista,ancho)
            row["apariencias"]=[apariencia(f,c) for c in row["caballos"]]
        pending.clear()
    try:
        while True:
            raw=proc.stdout.read(wa*ha*3)
            if len(raw)!=wa*ha*3:
                break
            f=np.frombuffer(raw,np.uint8).reshape(ha,wa,3).copy(); t=i/FPS_ANALISIS
            div=bool(len(td) and bd[min(np.searchsorted(td,t),len(bd)-1)])
            idx=int(np.argmin(abs(times-t))) if len(times) else None
            boxes=d["detecciones"][idx]["cajas"] if idx is not None and abs(times[idx]-t)<.32 else []
            cab,jin=cajas_utiles(boxes,div,pista,ancho)
            gray=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY)
            motion=None; coherence=0.0
            if prev is not None:
                mask=np.full(gray.shape,255,np.uint8)
                mask[:100]=0; mask[420:]=0
                if div: mask[base.PISTAS[pista]["corte"]//2:]=0
                for c in prev_cab:
                    x1,y1,x2,y2=[int(v/2) for v in c];mask[max(0,y1-8):y2+8,max(0,x1-8):x2+8]=0
                pts=cv2.goodFeaturesToTrack(prev,120,.02,12,mask=mask)
                if pts is not None:
                    nxt,st,_=cv2.calcOpticalFlowPyrLK(prev,gray,pts,None)
                    if nxt is not None:
                        flow=(nxt-pts).reshape(-1,2)[st.ravel()==1]
                        if len(flow)>12:
                            med=np.median(flow,axis=0);coherence=float(np.mean(np.linalg.norm(flow-med,axis=1)<6))
                            if coherence>.55: motion=[round(float(v*2),2) for v in med]
            critical=(max(0,partida-5)<=t<=partida+5 or final-17<=t<=meta+3)
            row={"t":t,"caballos":cab,"jinetes":jin,"dividida":div,
                 "movimiento":motion,"coherencia":round(coherence,3),
                 "partidor":partidor(f) if t<max(partida,4) else None,
                 "apariencias":[apariencia(f,c) for c in cab]}
            out.append(row)
            # Más detalle en los momentos críticos y ante pérdida de detección.
            if critical or (not cab and partida<=t<=meta): pending.append((len(out)-1,f))
            if len(pending)>=6: completar()
            prev,prev_cab=gray,cab;i+=1
            if i%160==0: print(f"  visión sol-6.1: {t:.0f}/{d['duracion']:.0f} s",flush=True)
        completar()
        code=proc.wait()
        if code or not out: raise RuntimeError("No se pudo decodificar el video para sol-6.1")
    finally:
        proc.stdout.close()
        if proc.poll() is None: proc.kill();proc.wait()
    guardar(path,{"firma":firma,"muestras":out,"version_partidor":2,"version_rescate":2})
    return out


def actualizar_partidor(video,rows,partida,ancho):
    """Migra sólo el reconocimiento inicial; no repite YOLO por cambiar esta heurística."""
    proc=subprocess.Popen(["ffmpeg","-v","error","-t",str(max(partida,4)),"-i",video,
        "-vf",f"fps=2,scale={ancho//2}:540","-f","rawvideo","-pix_fmt","bgr24","-"],
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    gates=[];tam=(ancho//2)*540*3
    try:
        while True:
            raw=proc.stdout.read(tam)
            if len(raw)!=tam:break
            gates.append(partidor(np.frombuffer(raw,np.uint8).reshape(540,ancho//2,3)))
        if proc.wait():raise RuntimeError("Error al revisar el partidor")
    finally:
        proc.stdout.close()
        if proc.poll() is None:proc.kill();proc.wait()
    for r in rows:
        if r["t"]<partida and gates:r["partidor"]=gates[min(int(r["t"]*2),len(gates)-1)]


def refinar_partida(video,rows,partida,ancho,pista):
    from ultralytics import YOLO
    detector=YOLO(str(Path(base.AQUI)/"modelos"/base.MODELO))
    cap=cv2.VideoCapture(video)
    try:
        for row in rows:
            if not max(0,partida-5)<=row["t"]<partida-2:continue
            cap.set(cv2.CAP_PROP_POS_MSEC,row["t"]*1000);ok,f=cap.read()
            if not ok:continue
            f=cv2.resize(f,(ancho//2,540))
            result=detector.predict(f,imgsz=1280,conf=.18,classes=[0,17],verbose=False)[0]
            boxes=[[round(v*2,1) for v in xy]+[round(conf,3),int(cls)]
                   for xy,conf,cls in zip(result.boxes.xyxy.tolist(),result.boxes.conf.tolist(),result.boxes.cls.tolist())]
            row["caballos"],row["jinetes"]=cajas_utiles(boxes,row["dividida"],pista,ancho)
            row["apariencias"]=[apariencia(f,c) for c in row["caballos"]]
    finally:cap.release()


class Seguimiento:
    """Asociación por toma, compensada con el movimiento del fondo; memoria corta ante oclusión."""
    def __init__(self):
        self.tracks={};self.next_id=1

    def actualizar(self,row,reset=False):
        t=row["t"]
        if reset: self.tracks.clear()
        self.tracks={k:v for k,v in self.tracks.items() if t-v["t"]<=1.25}
        current=[]; keys=list(self.tracks)
        boxes=row["caballos"]
        cost=np.full((len(keys),len(boxes)),1000.0)
        move=np.array(row.get("movimiento") or [0,0])
        for i,k in enumerate(keys):
            tr=self.tracks[k]; dt=t-tr["t"]
            pred=centro(tr["box"])+move+np.array(tr["vel"])*dt
            for j,c in enumerate(boxes):
                distance=np.linalg.norm(centro(c)-pred)/max(70,c[2]-c[0],tr["box"][2]-tr["box"][0])
                appearance=np.linalg.norm(np.array(tr["apariencia"])-row["apariencias"][j])
                cost[i,j]=distance+.45*appearance+.25*(1-iou(tr["box"],c))
        matches={}
        if cost.size:
            for i,j in zip(*linear_sum_assignment(cost)):
                if cost[i,j]<1.8: matches[j]=keys[i]
        for j,c in enumerate(boxes):
            k=matches.get(j)
            old=self.tracks.get(k)
            if k is None: k=self.next_id;self.next_id+=1
            dt=t-old["t"] if old else 0
            vel=((centro(c)-centro(old["box"])-move)/max(dt,.01)) if old else np.zeros(2)
            if old: vel=.4*vel+.6*np.array(old["vel"])
            tr={"id":k,"box":c,"t":t,"edad":old["edad"]+1 if old else 1,
                "vel":vel.tolist(),"apariencia":row["apariencias"][j]}
            self.tracks[k]=tr;current.append(tr)
        return current


def tomas_confirmadas(d, rows):
    """No confunde un paneo o un caballo corriendo con un cambio de cámara."""
    fps=d["fps"];mad=d["mad"]
    cuts=[]
    for c in base.candidatos_corte(d):
        frame=int(c*fps)
        near=mad[max(0,frame-2):frame+3]
        local=mad[max(0,frame-30):max(0,frame-3)]+mad[frame+4:frame+31]
        peak=max(near or [0]);median=float(np.median(local)) if local else 1
        if peak>18 and peak>max(2.4*median,median+12):
            if not cuts or c-cuts[-1]>1.2: cuts.append(round(c,3))
    return cuts


def direccion_toma(rows, cuts):
    """Vota dentro de cada toma y de ventanas locales; no hereda sentido entre cámaras."""
    for r in rows:
        r["toma"]=bisect.bisect_right(cuts,r["t"])
        r["orientacion"]=base.orientacion(r["caballos"],r["jinetes"])
    groups={}
    for r in rows: groups.setdefault(r["toma"],[]).append(r)
    for group in groups.values():
        for i,r in enumerate(group):
            near=group[max(0,i-6):i+7]
            votes=[s["orientacion"] for s in near if s["orientacion"] is not None and abs(s["orientacion"])>.045]
            consistency=abs(float(np.mean(np.sign(votes)))) if votes else 0
            direction=int(np.sign(np.median(votes))) if len(votes)>=2 and consistency>=.55 else 0
            ratios=[(c[3]-c[1])/max(c[2]-c[0],1) for c in r["caballos"]]
            spread=max((c[2] for c in r["caballos"]),default=0)-min((c[0] for c in r["caballos"]),default=0)
            frontal=bool(ratios) and np.median(ratios)>.92 and spread<.75*VENTANA
            # El paneo del fondo aporta evidencia, sin imponer el sentido en una toma frontal.
            flows=[s["movimiento"][0] for s in near if s.get("movimiento") and abs(s["movimiento"][0])>4]
            if not direction and not frontal and len(flows)>=4 and abs(np.mean(np.sign(flows)))>.7:
                direction=-int(np.sign(np.median(flows))); consistency=.6
            r["sentido"]=direction;r["frontal"]=frontal;r["conf_direccion"]=round(consistency,2)


def limites_lider(c, sentido, ancho):
    # Todo el caballo, con margen a ambos lados, siempre que físicamente quepa.
    margin=VENTANA*.07
    if c[2]-c[0]+margin*2>=VENTANA:
        x=float(centro(c)[0]);return x,x
    return max(VENTANA/2,c[2]+margin-VENTANA/2), min(ancho-VENTANA/2,c[0]-margin+VENTANA/2)


def planificar(rows,d,parciales,partida,recta=None,eventos=(),sin_recta=False):
    ancho=d["ancho"];dur=d["duracion"];base.W=ancho
    final=parciales[-2] if len(parciales)>=2 else dur*.65
    meta=parciales[-1] if parciales else dur*.78
    cuts=tomas_confirmadas(d,rows)
    line=base.detectar_meta(recta,min(final,dur*.65),cuts) if recta else None
    # La presencia del poste no equivale a haber cruzado la meta.
    if line: final=min(final,max(partida+8,line["inicio_toma"]))
    start_cues=[e["t"] for e in eventos if e["tipo"]=="partida" and abs(e["t"]-partida)<12]
    if start_cues: partida=min(start_cues,key=lambda t:abs(t-partida))
    # La gráfica o el comentario de una mala partida suelen llegar después de abrirse
    # las puertas. Busca la primera salida sostenida, sólo si antes había partidor.
    before=[r for r in rows if max(0,partida-6)<=r["t"]<partida-3]
    if any(r.get("partidor") for r in before) and before and np.median([len(r["caballos"]) for r in before])<2:
        launch=[r for r in rows if max(0,partida-5)<=r["t"]<=partida+1]
        for i in range(len(launch)-2):
            if all(len(r["caballos"])>=2 for r in launch[i:i+3]):
                partida=min(partida,launch[i]["t"]);break
    final_cues=[e["t"] for e in eventos if e["tipo"]=="recta" and meta-36<e["t"]<meta-4]
    if final_cues: final=min(final_cues)
    finish_cues=[e["t"] for e in eventos if e["tipo"]=="meta" and abs(e["t"]-meta)<5]
    if finish_cues: meta=min(finish_cues,key=lambda t:abs(t-meta))
    direccion_toma(rows,cuts)
    tracker=Seguimiento();last_shot=None;leader=None;pending=None;pending_since=0
    targets=[];notes=[];previous=ancho/2;speed=0;last_t=0;uncertain=[]
    # Partidor: consenso temporal y límite a los últimos segundos previos a la salida.
    gates=[r["partidor"][0] for r in rows if r.get("partidor") and r["t"]<partida]
    gate=float(np.median(gates)) if len(gates)>=4 else None
    if gate is None: notes.append("Partidor sin evidencia suficiente: se mantiene un encuadre estable; revisar inicio.")
    for row in rows:
        t=row["t"];dt=max(.01,t-last_t);reset=row["toma"]!=last_shot
        tracks=tracker.actualizar(row,reset)
        if reset: leader=None;pending=None
        last_shot=row["toma"]
        s=row["sentido"];cab=[tr["box"] for tr in tracks]
        phase="previa" if t<partida else "partida" if t<partida+7 else "carrera" if t<final-12 else "curva" if t<final else "recta" if t<=meta+1 else "despues"
        desired=previous;reason="mantener (sin detección)";confidence=0.0;bound=None
        if phase=="previa" and gate is not None:
            desired=gate;reason="partidor (consenso)";confidence=.65
        elif tracks:
            stable=[tr for tr in tracks if tr["edad"]>=2]
            candidates=stable or tracks
            if phase=="despues":
                # Continuidad visual, nunca identificar al ganador sólo por tamaño.
                remembered=next((tr for tr in candidates if tr["id"]==leader),None)
                chosen=remembered or min(candidates,key=lambda tr:abs(centro(tr["box"])[0]-previous))
                desired=float(centro(chosen["box"])[0]);reason="seguimiento tras meta";confidence=.65 if remembered else .4
                leader=chosen["id"]
            elif row["frontal"] or not s:
                desired=float(np.median([centro(c)[0] for c in cab]));reason="grupo (perspectiva ambigua)";confidence=.35
            else:
                ordered=sorted(candidates,key=lambda tr:s*(tr["box"][2] if s>0 else tr["box"][0]),reverse=True)
                candidate=ordered[0];current=next((tr for tr in candidates if tr["id"]==leader),None)
                # Confirmación temporal del adelantamiento; nunca bloquear un relevo real.
                if current and candidate["id"]!=leader:
                    if pending!=candidate["id"]:pending=candidate["id"];pending_since=t
                    delta=s*(centro(candidate["box"])[0]-centro(current["box"])[0])
                    if t-pending_since<.5 and delta<VENTANA*.3: candidate=current
                else: pending=None
                leader=candidate["id"];c=candidate["box"]
                head=c[2] if s>0 else c[0]
                desired=head-s*(VENTANA*.5-VENTANA*.12)
                # Si sólo hay uno o el siguiente ya no cabe, prioriza su cuerpo completo.
                if len(ordered)==1 or abs(centro(c)[0]-centro(ordered[1]["box"])[0])>VENTANA*.9:
                    desired=float(centro(c)[0])
                bound=limites_lider(c,s,ancho);confidence=min(.9,.5+.06*candidate["edad"])
                reason="punteros confirmados" if phase in ("curva","recta") else "puntero y perseguidores"
            if phase=="partida":
                # No excursiones al rezagado: lo incluye si cabe, sin abandonar a los punteros.
                lo=min(c[0] for c in cab);hi=max(c[2] for c in cab)
                if hi-lo<VENTANA*.85: desired=(lo+hi)/2;reason="salida del grupo"
        if sin_recta and final<=t<=meta+1:
            desired=next((r["x"] for r in reversed(targets) if r["fase"]!="recta"),previous)
            reason="recta manual";bound=None
        desired=float(np.clip(desired,VENTANA/2,ancho-VENTANA/2))
        if not targets: previous=desired
        # Cámara editorial: zona muerta, aceleración y velocidad limitadas, también tras la meta.
        vmax=230 if phase=="despues" else 360
        accel=280 if phase=="despues" else 520
        error=desired-previous
        want=0 if abs(error)<18 else float(np.clip(error/.65,-vmax,vmax))
        previous_speed=speed
        speed+=float(np.clip(want-speed,-accel*dt,accel*dt))
        speed=float(np.clip(speed,-vmax,vmax))
        x=previous+speed*dt
        if error*(desired-x)<0:x=desired;speed=0
        # Recuperación gradual ante cambio real de toma; no saltos añadidos por el editor.
        feasible=True
        if bound:
            reach=(previous+max(-vmax,previous_speed-accel*dt)*dt,
                   previous+min(vmax,previous_speed+accel*dt)*dt)
            lo=max(bound[0],reach[0]);hi=min(bound[1],reach[1])
            if lo<=hi:
                x=float(np.clip(x,lo,hi));speed=(x-previous)/dt
            else: feasible=False
        x=float(np.clip(x,VENTANA/2,ancho-VENTANA/2))
        if confidence<.5 or not feasible: uncertain.append(t)
        targets.append({"t":t,"x":round(x,2),"objetivo":round(desired,2),"motivo":reason,"fase":phase,
                        "confianza":round(confidence,2),"toma":row["toma"],"lider":leader,
                        "limites":list(bound) if bound else None,"encuadre_posible":feasible})
        previous=x;last_t=t
    if not targets: raise ValueError("El video no produjo muestras de seguimiento")
    # Puntos cada 0.25 s: reproducen la cámara calculada y sus límites. No se impone una
    # separación de 6 s capaz de sacar al puntero de cuadro en la curva.
    points=base.rdp([(r["t"],r["x"]) for r in targets],4.0)
    reasons={r["t"]:r["motivo"] for r in targets}
    kf=[[t,round(x,1),reasons[t]] for t,x in points]
    if kf[-1][0]<dur:kf.append([round(dur,3),kf[-1][1],"mantener"])
    review=intervalos(uncertain,.26,1.0)
    notes.append("No se añaden cortes de encuadre; los cambios de toma del video original permanecen.")
    notes.append("Las identidades de seguimiento son locales a cada toma, no nombres ni dorsales.")
    if review:notes.append(f"Revisión recomendada en {len(review)} tramo(s) de baja confianza.")
    return {"perfil":PERFIL,"version":VERSION,"keyframes":kf,"cortes":[],"cortes_fuente":cuts,
            "ancho":ancho,"ventana":VENTANA,"escala":base.ESCALA,"flash":False,"paneo_continuo":True,
            "serie":[[r["t"],r["x"],r["motivo"]] for r in targets],"seguimiento":targets,
            "notas":notes,"revisar":review,"sin_foco":[final,meta+1] if sin_recta else None,
            "recta":{"inicio":round(final,2),"meta":round(meta,2),"poste":line["x"] if line else None},
            "eventos_narrador":list(eventos),"partida":partida}


def intervalos(times,gap,minimo):
    groups=[]
    for t in times:
        if groups and t-groups[-1][1]<=gap: groups[-1][1]=round(t,2)
        else: groups.append([round(t,2),round(t,2)])
    return [g for g in groups if g[1]-g[0]>=minimo]


def vista_previa(video,keyframes,salida,ancho):
    """MP4 vertical con el audio original, para comprobar la sincronía del relato."""
    if os.path.normcase(os.path.abspath(video))==os.path.normcase(os.path.abspath(salida)):
        raise ValueError("La vista previa no puede reemplazar el video original")
    if Path(salida).exists():raise FileExistsError("La vista previa ya existe: "+salida)
    cap=cv2.VideoCapture(video)
    fps=cap.get(cv2.CAP_PROP_FPS) or 30
    crop_width=round(VENTANA/2)*2
    command=["ffmpeg","-v","error","-f","rawvideo","-pix_fmt","bgr24",
             "-s",f"{crop_width}x1080","-r",str(fps),"-i","pipe:0","-i",video,
             "-map","0:v:0","-map","1:a:0?","-c:v","libx264","-preset","veryfast",
             "-crf","23","-pix_fmt","yuv420p","-c:a","aac","-b:a","160k",
             "-shortest","-movflags","+faststart",salida]
    proc=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    i=0
    try:
        while True:
            ok,f=cap.read()
            if not ok:break
            f=cv2.resize(f,(ancho,1080))
            x=int(round(base.x_en(keyframes,i/fps)-crop_width/2))
            x=max(0,min(x,ancho-crop_width))
            proc.stdin.write(np.ascontiguousarray(f[:,x:x+crop_width]).tobytes());i+=1
        proc.stdin.close();error=proc.stderr.read().decode("utf-8",errors="replace")
        if proc.wait():raise RuntimeError("Error al crear vista previa: "+error)
    finally:
        cap.release();proc.stderr.close()
        if proc.poll() is None:proc.kill();proc.wait()


def main():
    try:sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:pass
    ap=argparse.ArgumentParser(description=PERFIL+": análisis visual y narración local")
    ap.add_argument("video");ap.add_argument("--salida",required=True)
    ap.add_argument("--pista",choices=sorted(base.PISTAS),default="rinconada")
    ap.add_argument("--parciales",default="");ap.add_argument("--distancias",default="")
    ap.add_argument("--partida",type=float,default=0)
    ap.add_argument("--narrador",choices=["auto","no","transcribir"],default="auto")
    ap.add_argument("--transcripcion",help="SRT o JSON con segmentos start/end/text")
    ap.add_argument("--desfase-narrador",type=float,default=0,help="segundos que sumar al audio para alinearlo con imagen")
    ap.add_argument("--sin-recta-final",action="store_true")
    ap.add_argument("--recalcular",action="store_true");ap.add_argument("--preview")
    a=ap.parse_args()
    d=base.cargar_o_analizar(a.video,base.PERFILES["flash"],a.pista)
    partials=[float(x) for x in a.parciales.split(",") if x.strip()]
    distances=[int(x) for x in a.distancias.split(",") if x.strip()]
    start,end=base.tramo_carrera(d,a.pista)
    estimated=not partials and bool(distances)
    if estimated:partials=base.parciales_por_distancia(d,distances,a.pista)
    departure=a.partida or start or 0
    final=partials[-2] if len(partials)>1 else d["duracion"]*.65
    finish=partials[-1] if partials else d["duracion"]*.78
    rail=base.cargar_o_analizar_recta(a.video,d,max(0,final-25),a.pista)
    trans=narracion(a.video,a.narrador,a.transcripcion)
    events=eventos_narrador(trans.get("segmentos",[]),a.desfase_narrador)
    rows=analizar_visual(a.video,d,departure,final,finish,a.pista,a.recalcular)
    r=planificar(rows,d,partials,departure,rail,events,a.sin_recta_final)
    r.update(dividida=base.intervalos_dividida(d["muestras"]),parciales=partials,
             parciales_estimados=estimated,carrera=[start,end],narracion=trans.get("estado"),
             video=os.path.abspath(a.video),duracion=d["duracion"])
    guardar(a.salida,r)
    report=Path(a.salida).with_suffix(".revision.md")
    report.write_text("# "+PERFIL+"\n\n"+"\n".join("- "+n for n in r["notas"])+
                      "\n\nNarración: "+str(r["narracion"])+"\n\nTramos para revisar (segundos):\n"+
                      "\n".join(f"- {lo:.2f}–{hi:.2f}" for lo,hi in r["revisar"])+"\n",encoding="utf-8")
    print(f"  {PERFIL}: {len(r['keyframes'])} puntos, sin saltos añadidos; narración: {r['narracion']}")
    for note in r["notas"]:print("  "+note)
    if a.preview:vista_previa(a.video,r["keyframes"],a.preview,d["ancho"])


if __name__=="__main__": main()
