"""Скачивает модель распознавания русской речи для голосового ввода.

Запустить один раз: python install_speech.py

Модель — Zipformer2 от авторов Vosk (ошибка распознавания 9.8% на Common Voice ru),
формат ONNX для движка sherpa-onnx. Работает без интернета, около 60 МБ.
"""

import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://huggingface.co/alphacep/vosk-model-small-ru/resolve/main/"
MODEL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TextHelper" / "speech-model"

# Слева — путь в репозитории, справа — как файл называется у нас.
FILES = {
    "am/encoder.int8.onnx": "encoder.onnx",
    "am/decoder.int8.onnx": "decoder.onnx",
    "am/joiner.int8.onnx": "joiner.onnx",
    "lang/tokens.txt": "tokens.txt",
    "test.wav": "test.wav",
}

ATTEMPTS = 3


def download(url, target):
    """Качает файл по частям, с повторами при обрыве связи."""
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                total = int(response.headers.get("Content-Length", 0))
                done = 0
                with open(target, "wb") as output:
                    while True:
                        chunk = response.read(262144)
                        if not chunk:
                            break
                        output.write(chunk)
                        done += len(chunk)
                        if total:
                            sys.stdout.write(
                                f"\r  {target.name}: {done / 1048576:.1f} из "
                                f"{total / 1048576:.1f} МБ ({done * 100 // total}%)"
                            )
                            sys.stdout.flush()
            print()
            return
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as error:
            print(f"\n  попытка {attempt} не удалась: {error}")
            if attempt == ATTEMPTS:
                raise
            time.sleep(3)


def install():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Модель распознавания речи -> {MODEL_DIR}\n")

    for remote, local in FILES.items():
        target = MODEL_DIR / local
        if target.exists() and target.stat().st_size > 0:
            print(f"  {local}: уже скачан")
            continue
        download(BASE_URL + remote, target)

    print("\nГотово. Голосовой ввод можно включать.")
    return MODEL_DIR


if __name__ == "__main__":
    install()
