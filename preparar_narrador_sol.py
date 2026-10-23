"""Descarga una vez los pesos de voz a modelos/. No transmite videos ni audio."""
from pathlib import Path
from faster_whisper.utils import download_model

if __name__ == "__main__":
    output=Path(__file__).parent/"modelos"/"faster-whisper-small"
    download_model("small",output_dir=str(output))
    print("Narrador local preparado:",output)
