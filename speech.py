"""Голосовой ввод: запись с микрофона и распознавание речи на месте.

Всё происходит на компьютере, без интернета и без сторонних служб.
Модель скачивается один раз командой: python install_speech.py
"""

import logging
import os
import threading
import time
import wave
from pathlib import Path

log = logging.getLogger("TextHelper")

MODEL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "TextHelper" / "speech-model"
SAMPLE_RATE = 16000
MAX_SECONDS = 120

_recognizer = None
_lock = threading.Lock()


class SpeechUnavailable(Exception):
    """Распознавание речи недоступно: нет модели или библиотеки."""


def available():
    """Модель скачана и готова к работе."""
    needed = ("encoder.onnx", "decoder.onnx", "joiner.onnx", "tokens.txt")
    return all((MODEL_DIR / name).exists() for name in needed)


def load():
    """Загружает модель. Первый вызов занимает пару секунд, дальше — мгновенно."""
    global _recognizer
    with _lock:
        if _recognizer is not None:
            return _recognizer
        if not available():
            raise SpeechUnavailable(
                "Модель распознавания речи не установлена.\n"
                "Запустите: python install_speech.py"
            )
        try:
            import sherpa_onnx
        except ImportError as error:
            raise SpeechUnavailable(
                "Не установлена библиотека sherpa-onnx.\n"
                "Запустите: pip install -r requirements.txt"
            ) from error

        _recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(MODEL_DIR / "encoder.onnx"),
            decoder=str(MODEL_DIR / "decoder.onnx"),
            joiner=str(MODEL_DIR / "joiner.onnx"),
            tokens=str(MODEL_DIR / "tokens.txt"),
            num_threads=2,
            sample_rate=SAMPLE_RATE,
            feature_dim=80,
            decoding_method="greedy_search",
        )
        return _recognizer


def preload():
    """Прогревает модель в фоне, чтобы первое нажатие микрофона не ждало загрузки."""
    def worker():
        try:
            load()
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def transcribe(samples, sample_rate=SAMPLE_RATE):
    """Превращает звук (numpy float32, моно) в текст."""
    recognizer = load()
    stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    recognizer.decode_stream(stream)
    return stream.result.text.strip()


def transcribe_wav(path):
    """Распознаёт WAV-файл. Нужно для проверки без микрофона."""
    import numpy as np

    with wave.open(str(path), "rb") as wav:
        frames = wav.readframes(wav.getnframes())
        rate = wav.getframerate()
    samples = np.frombuffer(frames, dtype=np.int16).astype("float32") / 32768.0
    return transcribe(samples, rate)


class Dictation:
    """Запись с микрофона до команды «стоп», затем распознавание.

    Работает в отдельном потоке. Окно опрашивает признак `done` и забирает `text`.
    """

    def __init__(self):
        self.text = ""
        self.error = None
        self.done = False
        self.recording = False
        self.level = 0.0      # громкость последнего кусочка, 0..1
        self.seconds = 0.0    # сколько уже записано
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            import numpy as np
            import sounddevice as sd

            log.info("Голос: загружаю модель")
            load()  # до начала записи, иначе потеряются первые слова

            chunks = []
            block = int(SAMPLE_RATE * 0.1)
            limit = MAX_SECONDS * 10
            log.info("Голос: начинаю запись")

            with sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32"
            ) as stream:
                self.recording = True
                while not self._stop.is_set() and len(chunks) < limit:
                    data, _overflowed = stream.read(block)
                    chunks.append(data.copy())
                    # Окно показывает это как полоску: видно, что вас слышат.
                    self.level = float(np.abs(data).max())
                    self.seconds = len(chunks) * 0.1
            self.recording = False

            if not chunks:
                log.warning("Голос: записи нет")
                return

            samples = np.concatenate(chunks).flatten()
            level = float(np.abs(samples).max())
            seconds = len(samples) / SAMPLE_RATE
            log.info("Голос: записано %.1f с, громкость %.3f", seconds, level)

            if level < 0.02:
                self.error = "Микрофон почти ничего не слышит. Говорите ближе и громче."
                return

            started = time.monotonic()
            self.text = transcribe(samples)
            log.info(
                "Голос: распознано за %.1f с — %r",
                time.monotonic() - started, self.text[:80],
            )
        except SpeechUnavailable as error:
            log.error("Голос: %s", error)
            self.error = str(error)
        except Exception as error:
            log.exception("Голос: сбой записи")
            self.error = f"Микрофон недоступен: {error}"
        finally:
            self.recording = False
            self.done = True
