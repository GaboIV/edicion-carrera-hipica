"""Verificación independiente del montaje JSON y la trayectoria escrita en Camtasia.

Uso: python validar_proyecto_sol.py archivo.tscproj archivo-foco.json
No sustituye abrir/exportar en el editor nativo.
"""
import json
from pathlib import Path
import sys
from foco_carrera import x_en
from camtasia_carrera import problemas_animaciones_visuales


def validar(project,focus):
    project=Path(project);p=json.loads(project.read_text(encoding="utf-8-sig"))
    f=json.loads(Path(focus).read_text(encoding="utf-8"));er=p["editRate"]
    errors=[];ids=set();main=[];count=0
    for source in p["sourceBin"]:
        if not (project.parent/source["src"]).exists():errors.append("Medio ausente: "+source["src"])
    def walk(o):
        nonlocal count
        if isinstance(o,dict):
            errors.extend(problemas_animaciones_visuales(o))
            if o.get("_type") in ("IMFile","VMFile","AMFile","UnifiedMedia"):
                if o["id"] in ids:errors.append(f"ID duplicado {o['id']}")
                ids.add(o["id"])
                if o.get("duration",1)<=0:errors.append("Duración no positiva")
            if o.get("_type")=="VMFile":
                tx=o.get("parameters",{}).get("translation0")
                if isinstance(tx,dict) and len(tx.get("keyframes",[]))>20:main.append(o)
            for name,param in o.get("parameters",{}).items():
                if not isinstance(param,dict):continue
                last=0
                for k in param.get("keyframes",[]):
                    count+=1
                    if not 0<=last<=k["time"]<k["endTime"]<=o["duration"]:errors.append("Keyframe fuera de orden/rango: "+name)
                    if k["duration"]!=k["endTime"]-k["time"]:errors.append("Duración inválida: "+name)
                    last=k["endTime"]
            for v in o.values():walk(v)
        elif isinstance(o,list):
            for v in o:walk(v)
    walk(p["timeline"])
    if len(main)!=1:errors.append(f"Se esperaba un clip principal animado; encontrados {len(main)}")
    maximum=0
    if len(main)==1:
        clip=main[0];param=clip["parameters"]["translation0"]
        if param.get("interp")!="linr":errors.append("Interpolación del foco no lineal")
        scale=p["height"]/1080
        def value(t):
            value=param["defaultValue"]
            for k in param["keyframes"]:
                if t<k["time"]:return value
                if t<k["endTime"]:
                    return value+(k["value"]-value)*(t-k["time"])/(k["endTime"]-k["time"])
                value=k["value"]
            return value
        for sample in f["seguimiento"]:
            time=sample["t"]*er-clip.get("mediaStart",0)
            if not 0<=time<=clip["duration"]:continue
            native=f["ancho"]/2-value(time)/scale
            expected=x_en(f["keyframes"],sample["t"])
            maximum=max(maximum,abs(native-expected))
        if maximum>10:errors.append(f"Diferencia de encuadre JSON/vista previa: {maximum:.2f}px")
    if errors:raise ValueError("\n".join(errors))
    return {"proyecto":str(project),"medios":len(p["sourceBin"]),"clips":len(ids),
            "keyframes":count,"diferencia_maxima_px":round(maximum,3),"estado":"validación estática correcta"}


if __name__=="__main__":
    print(json.dumps(validar(*sys.argv[1:]),ensure_ascii=False,indent=2))
