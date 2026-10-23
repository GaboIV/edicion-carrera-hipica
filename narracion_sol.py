# -*- coding: utf-8 -*-
"""Transcripción local opcional y señales conservadoras del relato hípico."""
import json
from pathlib import Path
import re
import subprocess
import unicodedata
import numpy as np


def normalizar(texto):
    return " ".join("".join(c for c in unicodedata.normalize("NFD",texto.lower())
                             if unicodedata.category(c)!="Mn").split())


def leer_transcripcion(path):
    path=Path(path)
    if path.suffix.lower()==".json":
        d=json.loads(path.read_text(encoding="utf-8-sig"))
        segments=d if isinstance(d,list) else d.get("segmentos",d.get("segments",[]))
    else:
        text=path.read_text(encoding="utf-8-sig")
        pattern=r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)[^\n]*\n(.*?)(?=\n\s*\n|\Z)"
        def seconds(s):
            h,m,s=s.replace(",",".").split(":");return int(h)*3600+int(m)*60+float(s)
        segments=[{"start":seconds(m[0]),"end":seconds(m[1]),"text":re.sub(r"<[^>]+>","",m[2]).replace("\n"," ")}
                  for m in re.findall(pattern,text,re.S)]
    for s in segments:
        if not all(k in s for k in ("start","end","text")) or float(s["end"])<float(s["start"]):
            raise ValueError("Transcripción inválida: se necesitan start, end y text")
    return segments


def narracion(video,modo="auto",archivo=None):
    if modo=="no":return {"estado":"desactivada","segmentos":[]}
    if archivo:
        return {"estado":"transcripción proporcionada","segmentos":leer_transcripcion(archivo)}
    video=Path(video)
    for ext in (".srt",".narracion.json"):
        candidate=video.with_suffix(ext)
        if candidate.exists():
            return {"estado":"transcripción junto al video","segmentos":leer_transcripcion(candidate)}
    cache=video.with_suffix(".narracion-sol.json")
    stat=video.stat()
    signature={"nombre":video.name,"bytes":stat.st_size,"mtime_ns":stat.st_mtime_ns,"version":1}
    if cache.exists():
        d=json.loads(cache.read_text(encoding="utf-8"))
        if d.get("firma")==signature:return d
    model_path=Path(__file__).parent/"modelos"/"faster-whisper-small"
    if not (model_path/"model.bin").exists():
        message="modelo local de voz no instalado; se usa sólo visión"
        if modo=="transcribir":raise RuntimeError(message+". Ejecuta preparar_narrador_sol.py primero.")
        return {"estado":message,"segmentos":[]}
    try:
        from faster_whisper import WhisperModel
        # No descarga implícita ni envío del audio. Los pesos se preparan por separado.
        model=WhisperModel(str(model_path),device="cpu",compute_type="int8",cpu_threads=4,local_files_only=True)
        print("  escuchando narración local...",flush=True)
        decoded=subprocess.run(["ffmpeg","-v","error","-i",str(video),"-vn","-ac","1",
                                "-ar","16000","-f","f32le","-"],capture_output=True,check=True)
        audio=np.frombuffer(decoded.stdout,dtype=np.float32).copy()
        segments,info=model.transcribe(audio,language="es",beam_size=5,vad_filter=True,
                                       word_timestamps=True,condition_on_previous_text=False)
        result=[]
        for s in segments:
            if s.no_speech_prob>.75 or s.avg_logprob < -1.2:continue
            result.append({"start":round(s.start,3),"end":round(s.end,3),"text":s.text,
                           "words":[{"start":round(w.start,3),"end":round(w.end,3),"word":w.word,
                                     "probability":round(w.probability,3)} for w in (s.words or [])]})
        d={"firma":signature,"estado":"transcripción local small/int8","segmentos":result}
        cache.write_text(json.dumps(d,ensure_ascii=False,indent=1),encoding="utf-8")
        return d
    except ImportError as e:
        if modo=="transcribir":raise RuntimeError("Falta faster-whisper en el Python de visión") from e
        return {"estado":"transcriptor no disponible; se usa sólo visión","segmentos":[]}


def eventos_narrador(segmentos,desfase=0):
    """Usa palabras con tiempo, evita asignar al comienzo de una frase larga un aviso tardío."""
    patterns={
        "partida":r"\b(partida|largaron|arrancaron|se abren las puertas|orden de partida)\b",
        "recta":r"\b(recta final|ultima recta|tierra derecha|entran (?:en |a )?la recta)\b",
        "meta":r"\b(cruza(?:n)? (?:la )?meta|cruzo (?:la )?meta|gano|ganando|en la raya|cruza el disco)\b",
    }
    events=[]
    for segment in segmentos:
        words=segment.get("words") or []
        tokens=[]
        for w in words:
            if w.get("probability",1)<.35:continue
            for token in normalizar(w["word"]).split():tokens.append((token,float(w["start"])))
        text=normalizar(segment["text"])
        for kind,pattern in patterns.items():
            # El texto y los tokens pueden diferir por la limpieza de puntuación.
            timed_text=" ".join(p[0] for p in tokens)
            match=re.search(pattern,timed_text) if tokens else re.search(pattern,text)
            if not match:continue
            t=float(segment["start"])
            if tokens:
                target=match.group(0)
                for i in range(len(tokens)):
                    phrase=" ".join(p[0] for p in tokens[i:i+len(target.split())])
                    if target in phrase:t=tokens[i][1];break
            if not events or not any(e["tipo"]==kind and abs(e["t"]-(t+desfase))<3 for e in events):
                events.append({"t":round(max(0,t+desfase),3),"tipo":kind,"texto":segment["text"].strip(),
                               "uso":"fase; no identidad del ejemplar"})
    return sorted(events,key=lambda e:e["t"])
