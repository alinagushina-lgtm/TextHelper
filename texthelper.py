"""TextHelper — помощник для обработки текста через Claude Code.

Живёт иконкой в системном трее Windows. Пользователь копирует текст,
кликает по иконке, выбирает действие — и получает результат в окне
«БЫЛО → СТАЛО» с возможностью доработки.

Запуск: python texthelper.py
"""

import ctypes
import functools
import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

import pyperclip
import pystray
from PIL import Image, ImageDraw

import speech
import ui

APP_NAME = "TextHelper"
BASE_DIR = Path(__file__).resolve().parent
DEFAULTS_PATH = BASE_DIR / "settings.default.json"
APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
SETTINGS_PATH = APP_DIR / "settings.json"
LOG_PATH = APP_DIR / "texthelper.log"

SHOW_FLAG = APP_DIR / ".show_panel"
MUTEX_NAME = "TextHelper.SingleInstance"
CREATE_NO_WINDOW = 0x08000000
ERROR_ALREADY_EXISTS = 183

SYSTEM_PROMPT = (
    "Ты обрабатываешь текст по заданию пользователя. "
    "Возвращай ТОЛЬКО готовый текст: без вступлений, без пояснений, "
    "без комментариев и без markdown-разметки. "
    "Никогда не задавай встречных вопросов и не проси прислать текст — "
    "весь нужный текст уже есть в сообщении."
)

log = logging.getLogger(APP_NAME)


# --------------------------------------------------------------------------
# Настройки
# --------------------------------------------------------------------------

class SettingsError(Exception):
    """Файл настроек испорчен."""


def load_settings():
    """Читает настройки пользователя, при первом запуске создаёт их из эталона."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not SETTINGS_PATH.exists():
        shutil.copyfile(DEFAULTS_PATH, SETTINGS_PATH)
        log.info("Создан файл настроек %s", SETTINGS_PATH)

    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SettingsError(str(error)) from error

    if not settings.get("actions"):
        raise SettingsError("В настройках нет ни одного действия")
    return settings


def load_defaults():
    return json.loads(DEFAULTS_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Вызов Claude Code
# --------------------------------------------------------------------------

class ClaudeError(Exception):
    """Понятная пользователю ошибка вызова Claude Code."""


def find_claude():
    """Ищет claude.exe: сначала в PATH, потом в стандартном месте установки."""
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude.exe"
    return str(fallback) if fallback.exists() else None


def build_first_prompt(action_prompt, text):
    return (
        f"Задача: {action_prompt}\n\n"
        "=== ТЕКСТ ===\n"
        f"{text}\n"
        "=== КОНЕЦ ТЕКСТА ==="
    )


def build_image_prompt(action_prompt, image_path):
    """Текста у нас нет — он на картинке, и прочитать её должен сам Claude."""
    return (
        f"Прочитай весь текст с картинки {image_path.name} "
        "(она лежит в текущей папке) и выполни с ним задачу.\n\n"
        f"Задача: {action_prompt}\n\n"
        "Верни только готовый текст — без описания картинки и без пояснений."
    )


def build_refine_prompt(action_prompt, original, previous, note, image_path=None):
    """Полный контекст в каждом запросе: claude -p не помнит прошлых вызовов."""
    if image_path is not None:
        source = (
            f"Исходный текст находится на картинке {image_path.name} "
            "в текущей папке — при необходимости перечитай её."
        )
    else:
        source = f"=== ИСХОДНЫЙ ТЕКСТ ===\n{original}\n=== КОНЕЦ ==="

    return (
        f"Исходная задача: {action_prompt}\n\n"
        f"{source}\n\n"
        "=== ПРЕДЫДУЩИЙ РЕЗУЛЬТАТ ===\n"
        f"{previous}\n"
        "=== КОНЕЦ ===\n\n"
        f"Доработай предыдущий результат с учётом замечания: {note}\n"
        "Верни только новый вариант текста."
    )


def clean_response(text):
    """Убирает обрамляющие markdown-кавычки, если модель всё-таки их добавила."""
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2:
            text = "\n".join(lines[1:-1]).strip()
    return text


def run_claude(prompt, model, timeout, image_path=None):
    """Запускает claude -p и возвращает готовый текст.

    Для обычного текста инструменты отключены полностью — так быстрее и
    безопаснее. Для картинки разрешаем единственный инструмент чтения:
    иначе Claude не сможет её открыть.
    """
    executable = find_claude()
    if executable is None:
        raise ClaudeError(
            "Claude Code не найден. Установите его и выполните вход командой "
            "claude login.\nИскали в PATH и в "
            f"{Path.home() / '.local' / 'bin' / 'claude.exe'}"
        )

    if image_path is None:
        # Текст обрабатывается без инструментов — быстрее и не лезет в файлы.
        access = ["--tools", ""]
    else:
        # Картинку нужно открыть, поэтому разрешаем единственный инструмент —
        # чтение файла, и заранее подтверждаем его, чтобы не ждать вопроса.
        access = ["--allowed-tools", "Read", "--permission-mode", "acceptEdits"]

    command = [
        executable, "-p",
        "--model", model,
        *access,
        "--strict-mcp-config",
        "--system-prompt", SYSTEM_PROMPT,
    ]

    # Отдельная временная папка, чтобы Claude Code не подхватил CLAUDE.md
    # и настройки постороннего проекта. Для картинки работаем в её папке,
    # иначе Claude до файла не дотянется.
    with tempfile.TemporaryDirectory(prefix="texthelper-") as tempdir:
        workdir = str(image_path.parent) if image_path is not None else tempdir
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
                cwd=workdir,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise ClaudeError(
                f"Claude не ответил за {timeout} секунд. Попробуйте ещё раз."
            ) from error
        except OSError as error:
            raise ClaudeError(f"Не удалось запустить Claude Code: {error}") from error

    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        log.error("claude вернул код %s: %s", completed.returncode, details)
        lowered = details.lower()
        if "login" in lowered or "authenticat" in lowered or "unauthorized" in lowered:
            raise ClaudeError(
                "Похоже, вход в Claude Code не выполнен. "
                "Откройте терминал и выполните: claude login"
            )
        raise ClaudeError(details[:600] or "Claude Code завершился с ошибкой.")

    result = clean_response(completed.stdout)
    if not result:
        raise ClaudeError("Claude вернул пустой ответ. Попробуйте ещё раз.")
    return result


# --------------------------------------------------------------------------
# Иконки
# --------------------------------------------------------------------------

VIOLET = (91, 91, 214, 255)
ORANGE = (230, 126, 34, 255)
WHITE = (255, 255, 255, 255)


def _plate(color):
    """Залитая подложка со скруглёнными углами: в трее значок всего 16 пикселей,
    поэтому тонкий контурный рисунок там не читается."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([2, 2, 62, 62], radius=14, fill=color)
    return image, draw


SKIN = (242, 200, 165, 255)
HAIR = (248, 248, 250, 255)
DARK = (60, 50, 60, 255)
TONGUE = (232, 96, 122, 255)
TONGUE_DARK = (198, 68, 96, 255)


def make_idle_icon():
    """Растрёпанный профессор с высунутым языком на фиолетовом фоне.

    Рисуем в большом размере и уменьшаем: так края получаются гладкими,
    а в трее значок занимает всего 16 пикселей.
    """
    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([6, 6, size - 6, size - 6], radius=56, fill=VIOLET)

    def circle(x, y, r, fill):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)

    # Копна волос — облако из кругов за головой, нарочно неровное.
    for x, y, r in [(70, 92, 44), (128, 66, 50), (186, 92, 44),
                    (52, 138, 34), (204, 138, 34), (128, 96, 56),
                    (34, 96, 22), (222, 96, 22), (92, 44, 26),
                    (166, 40, 22), (40, 62, 18), (216, 60, 16)]:
        circle(x, y, r, HAIR)

    circle(52, 150, 18, SKIN)   # уши
    circle(204, 150, 18, SKIN)

    draw.ellipse([62, 92, 194, 232], fill=SKIN)  # лицо

    # Волосы падают на лоб поверх лица.
    for x, y, r in [(78, 96, 30), (128, 84, 34), (178, 96, 30)]:
        circle(x, y, r, HAIR)

    circle(103, 148, 13, DARK)  # глаза
    circle(153, 148, 13, DARK)
    circle(107, 144, 4, WHITE)
    circle(157, 144, 4, WHITE)

    draw.ellipse([116, 158, 140, 182], fill=(228, 180, 146, 255))  # нос

    # Рот и высунутый язык: язык шире у основания, с закруглённым кончиком
    # и продольной складкой посередине.
    draw.rounded_rectangle([98, 186, 158, 212], radius=13, fill=DARK)
    draw.rounded_rectangle([108, 200, 148, 248], radius=20, fill=TONGUE)
    draw.line([(128, 212), (128, 238)], fill=TONGUE_DARK, width=5)

    return image.resize((64, 64), Image.LANCZOS)


def make_busy_icon():
    """Белые песочные часы на оранжевом — идёт обработка."""
    image, draw = _plate(ORANGE)
    draw.rectangle([16, 10, 48, 16], fill=WHITE)
    draw.rectangle([16, 48, 48, 54], fill=WHITE)
    draw.polygon([(20, 16), (44, 16), (32, 32)], fill=WHITE)
    draw.polygon([(32, 32), (20, 48), (44, 48)], fill=WHITE)
    return image


def already_running():
    """Второй запуск не нужен: одно приложение — одна панель и одна иконка.

    Проверяем через именованный мьютекс Windows. Если он уже создан другим
    процессом, значит, приложение работает.
    """
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not handle:
        return False
    if ctypes.windll.kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        return True
    # Держим ручку до конца работы процесса, иначе мьютекс исчезнет.
    globals()["_mutex_handle"] = handle
    return False


def request_show_panel():
    """Просит уже запущенный экземпляр показать панель."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SHOW_FLAG.write_text("show", encoding="utf-8")


# --------------------------------------------------------------------------
# Получение текста: выделение в чужом окне
# --------------------------------------------------------------------------

_user32 = ctypes.windll.user32
VK_CONTROL = 0x11
VK_C = 0x43
KEYEVENTF_KEYUP = 0x02


def foreground_window():
    """Окно, с которым пользователь работал до клика по панели."""
    return _user32.GetForegroundWindow()


def window_pid(hwnd):
    pid = ctypes.c_ulong()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _send_ctrl_c():
    _user32.keybd_event(VK_CONTROL, 0, 0, 0)
    _user32.keybd_event(VK_C, 0, 0, 0)
    _user32.keybd_event(VK_C, 0, KEYEVENTF_KEYUP, 0)
    _user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}


def clipboard_image():
    """Картинка из буфера обмена, если она там есть.

    Туда она попадает после Win+Shift+S, PrintScreen или копирования
    файла с картинкой в проводнике.
    """
    try:
        from PIL import ImageGrab
        content = ImageGrab.grabclipboard()
    except Exception:
        log.exception("Не удалось прочитать картинку из буфера обмена")
        return None

    if isinstance(content, list):
        # Скопирован файл в проводнике — берём первый, если это картинка.
        for name in content:
            path = Path(name)
            if path.suffix.lower() in IMAGE_SUFFIXES and path.exists():
                try:
                    from PIL import Image
                    return Image.open(path)
                except Exception:
                    log.exception("Не удалось открыть %s", path)
        return None

    return content if content is not None and hasattr(content, "save") else None


def save_clipboard_image(image):
    """Кладёт картинку в файл рядом с настройками — Claude читает её с диска."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    path = APP_DIR / "clip.png"
    image.convert("RGB").save(path)
    return path


def grab_input(previous_hwnd, allow_image=False, timeout=0.8):
    """Берёт то, с чем работать: выделенный текст или картинку из буфера.

    Пользователь выделяет текст и жмёт кнопку, не нажимая Ctrl+C, — значит,
    копируем за него: возвращаем фокус прежнему окну и посылаем ему Ctrl+C.

    Буфер при этом не очищаем: иначе пропала бы картинка, скопированная
    через Win+Shift+S. Понять, что появилось новое выделение, можно и так —
    по тому, что содержимое буфера изменилось.

    Возвращает пару: ("text", строка), ("image", картинка) или (None, None).
    """
    image = clipboard_image() if allow_image else None
    before = pyperclip.paste()

    if previous_hwnd:
        _user32.SetForegroundWindow(previous_hwnd)
        time.sleep(0.12)
    _send_ctrl_c()

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.05)
        selected = pyperclip.paste()
        if selected.strip() and selected != before:
            return "text", selected

    # Нового выделения нет: работаем с тем, что уже лежало в буфере.
    if image is not None:
        return "image", image
    if before.strip():
        return "text", before
    return None, None


def save_ico():
    """Сохраняет иконку в .ico — она нужна ярлыку на рабочем столе.

    Имя файла содержит отпечаток картинки. Иначе Windows показывает старый
    значок из своего кэша: он запоминает иконки по пути к файлу и не замечает,
    что содержимое поменялось.
    """
    image = make_idle_icon()
    stamp = hashlib.md5(image.tobytes()).hexdigest()[:8]
    path = APP_DIR / f"icon-{stamp}.ico"

    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        image.save(path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])

    for old in APP_DIR.glob("icon-*.ico"):
        if old != path:
            old.unlink(missing_ok=True)
    return path


# --------------------------------------------------------------------------
# Приложение
# --------------------------------------------------------------------------

class Conversation:
    """Контекст одного окна: что обрабатывали и что получилось."""

    def __init__(self, action, original):
        self.action = action
        self.original = original
        self.last_result = ""
        self.image_path = None  # задан, если обрабатываем картинку


class App:
    def __init__(self):
        self.settings, self.settings_error = self._load_settings_safely()
        self.conversations = {}
        self.actions_window = None
        self.busy = 0
        self._ticks = 0
        self.last_hwnd = 0
        self.tasks = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()

        self.idle_icon = make_idle_icon()
        self.busy_icon = make_busy_icon()
        self.icon = pystray.Icon(
            APP_NAME, self.idle_icon, APP_NAME, menu=self._build_menu()
        )
        _menu_on_left_click(self.icon)

    # --- инфраструктура ---------------------------------------------------

    def _load_settings_safely(self):
        try:
            return load_settings(), None
        except (SettingsError, OSError) as error:
            log.error("Настройки не прочитаны: %s", error)
            return load_defaults(), str(error)

    def post(self, callback):
        """Передаёт работу в главный поток: Tkinter не любит чужие потоки."""
        self.tasks.put(callback)

    def _pump(self):
        while True:
            try:
                callback = self.tasks.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                log.exception("Ошибка при обработке задачи")

        self._ticks += 1

        # Запоминаем чужое окно, чтобы потом забрать из него выделенный текст.
        if self._ticks % 3 == 0:
            hwnd = foreground_window()
            if hwnd and window_pid(hwnd) != os.getpid():
                self.last_hwnd = hwnd

        if self._ticks % 6 == 0 and SHOW_FLAG.exists():
            # Повторный запуск с ярлыка: показываем панель вместо второй копии.
            try:
                SHOW_FLAG.unlink()
            except OSError:
                pass
            self.show_actions()

        self.root.after(80, self._pump)

    def _set_busy(self, delta):
        self.busy = max(0, self.busy + delta)
        self.icon.icon = self.busy_icon if self.busy else self.idle_icon

    # --- меню трея --------------------------------------------------------

    def _handler(self, callback):
        """Обработчик пункта меню. pystray допускает не больше двух аргументов."""

        def wrapped(_icon, _item):
            self.post(callback)

        return wrapped

    def _build_menu(self):
        items = [pystray.MenuItem("✦ Меню действий", self._handler(self.show_actions))]
        items.append(pystray.Menu.SEPARATOR)
        items += [
            pystray.MenuItem(
                action["title"],
                self._handler(functools.partial(self.start_action, action)),
            )
            for action in self.settings["actions"]
        ]
        items.append(pystray.Menu.SEPARATOR)
        items.append(
            pystray.MenuItem("⚙️ Настройки", self._handler(self.open_settings))
        )
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("❌ Выход", self._handler(self.quit)))
        return pystray.Menu(*items)

    def open_settings(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if not SETTINGS_PATH.exists():
            shutil.copyfile(DEFAULTS_PATH, SETTINGS_PATH)
        process = subprocess.Popen(["notepad.exe", str(SETTINGS_PATH)])
        threading.Thread(
            target=self._reload_after_editing, args=(process,), daemon=True
        ).start()

    def _reload_after_editing(self, process):
        process.wait()
        self.post(self.reload_settings)

    def reload_settings(self):
        try:
            self.settings = load_settings()
        except (SettingsError, OSError) as error:
            ui.show_message(
                self.root,
                "Ошибка в настройках",
                "Файл настроек не читается, работаю на прежних настройках.\n\n"
                f"{error}",
                kind="error",
            )
            return
        self.icon.menu = self._build_menu()
        self.icon.update_menu()
        if self.actions_window is not None and self.actions_window.winfo_exists():
            self._hide_actions()
            self.show_actions()
        log.info("Настройки перечитаны")

    def quit(self):
        self.icon.stop()
        self.root.quit()

    # --- основной сценарий ------------------------------------------------

    def start_action(self, action):
        allow_image = bool(action.get("images"))
        try:
            kind, content = grab_input(self.last_hwnd, allow_image=allow_image)
        except Exception as error:
            log.exception("Буфер обмена недоступен")
            ui.show_message(
                self.root, "Буфер обмена",
                f"Не удалось прочитать буфер обмена: {error}", kind="error",
            )
            return

        if kind == "image":
            self._start_image_action(action, content)
            return

        text = content
        if not isinstance(text, str) or not text.strip():
            if self.actions_window is not None and self.actions_window.winfo_exists():
                hint = ("Выделите текст или скопируйте картинку" if allow_image
                        else "Выделите текст в документе")
                self.actions_window.flash(hint)
            else:
                ui.show_message(
                    self.root, "Буфер обмена пуст",
                    "В буфере обмена нет текста. Скопируйте текст и попробуйте снова.",
                )
            return

        limit = int(self.settings.get("max_input_chars", 10000))
        if len(text) > limit:
            proceed = ui.ask_yes_no(
                self.root, "Текст слишком длинный",
                f"В буфере {len(text)} символов, а предел — {limit}.\n"
                f"Обработать первые {limit} символов?",
                yes_text="Обработать", no_text="Отмена",
            )
            if not proceed:
                return
            text = text[:limit]

        conversation = Conversation(action, text)
        window = ui.ResultWindow(self.root, action["title"], text, self.refine)
        window.protocol(
            "WM_DELETE_WINDOW", lambda w=window: self._close_window(w)
        )
        self.conversations[window] = conversation

        window.set_busy()
        prompt = build_first_prompt(action["prompt"], text)
        self._request(window, prompt, ui.plain(action["title"]))

    def _start_image_action(self, action, image):
        """Тот же сценарий, но исходник — картинка из буфера обмена."""
        try:
            path = save_clipboard_image(image)
        except Exception as error:
            log.exception("Не удалось сохранить картинку")
            ui.show_message(
                self.root, "Картинка",
                f"Не удалось сохранить картинку из буфера: {error}", kind="error",
            )
            return

        width, height = image.size
        note = (
            f"Картинка из буфера обмена, {width}×{height} точек.\n\n"
            "Claude прочитает текст с картинки сам — результат появится ниже."
        )
        log.info("Картинка %d×%d, действие: %s", width, height, ui.plain(action["title"]))

        conversation = Conversation(action, note)
        conversation.image_path = path
        window = ui.ResultWindow(self.root, action["title"], note, self.refine)
        window.protocol("WM_DELETE_WINDOW", lambda w=window: self._close_window(w))
        self.conversations[window] = conversation

        window.set_busy()
        prompt = build_image_prompt(action["prompt"], path)
        self._request(window, prompt, ui.plain(action["title"]), image_path=path)

    def refine(self, window, note):
        conversation = self.conversations.get(window)
        if conversation is None:
            return
        window.set_busy()
        prompt = build_refine_prompt(
            conversation.action["prompt"],
            conversation.original,
            conversation.last_result,
            note,
            image_path=conversation.image_path,
        )
        self._request(
            window, prompt, "Доработка", image_path=conversation.image_path
        )

    def _request(self, window, prompt, label, image_path=None):
        model = self.settings.get("model", "sonnet")
        timeout = int(self.settings.get("timeout_seconds", 60))
        self._set_busy(+1)

        def worker():
            started = time.monotonic()
            try:
                result = run_claude(prompt, model, timeout, image_path=image_path)
                log.info(
                    "%s: %d символов, модель %s, ответ за %.1f c",
                    label, len(prompt), model, time.monotonic() - started,
                )
                self.post(lambda: self._deliver(window, result, None))
            except ClaudeError as error:
                message = str(error)
                log.warning("%s: ошибка за %.1f c — %s",
                            label, time.monotonic() - started, message)
                self.post(lambda: self._deliver(window, None, message))
            except Exception as error:
                log.exception("Непредвиденная ошибка при вызове Claude")
                message = f"Непредвиденная ошибка: {error}"
                self.post(lambda: self._deliver(window, None, message))

        threading.Thread(target=worker, daemon=True).start()

    def _deliver(self, window, result, error):
        self._set_busy(-1)
        if not window.winfo_exists():
            return
        if error is not None:
            window.set_error(error)
            ui.show_message(self.root, "Ошибка", error, kind="error")
            return
        conversation = self.conversations.get(window)
        if conversation is not None:
            conversation.last_result = result
        window.set_result(result)

    def _close_window(self, window):
        self.conversations.pop(window, None)
        window.destroy()

    # --- запуск -----------------------------------------------------------

    def show_actions(self):
        """Окно со списком действий. Появляется при запуске и по пункту меню."""
        if self.actions_window is not None and self.actions_window.winfo_exists():
            self.actions_window.deiconify()
            self.actions_window.attributes("-topmost", True)
            self.actions_window.lift()
            return
        # Если построение панели сорвётся на середине, недостроенное окно
        # останется висеть на экране. Поэтому запоминаем, что было до, и
        # убираем всё лишнее при сбое.
        before = set(self.root.winfo_children())
        try:
            self.actions_window = ui.ActionsWindow(
                self.root,
                self.settings["actions"],
                on_pick=self.start_action,
                on_close=self._hide_actions,
                on_quit=self.quit,
            )
        except Exception:
            self.actions_window = None
            for widget in set(self.root.winfo_children()) - before:
                widget.destroy()
            raise

    def _hide_actions(self):
        if self.actions_window is not None and self.actions_window.winfo_exists():
            self.actions_window.destroy()
        self.actions_window = None

    def run(self):
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.root.after(80, self._pump)
        self.post(self.show_actions)
        speech.preload()  # чтобы первое нажатие «Голос» не ждало загрузки модели
        if self.settings_error:
            self.post(
                lambda: ui.show_message(
                    self.root, "Ошибка в настройках",
                    "Файл настроек не читается, работаю на настройках по умолчанию."
                    f"\n\n{self.settings_error}",
                    kind="error",
                )
            )
        self.root.mainloop()
        self.icon.stop()


def _menu_on_left_click(icon):
    """Открывает меню по левому клику.

    pystray на Windows показывает меню только по правой кнопке, а по левой
    вызывает действие по умолчанию. Подменяем обработчик сообщения так,
    чтобы левый клик вёл себя как правый.
    """
    try:
        from pystray._util import win32
    except ImportError:
        return

    original = icon._on_notify

    def patched(wparam, lparam):
        if lparam == win32.WM_LBUTTONUP:
            log.info("Клик по иконке (левая кнопка)")
            lparam = win32.WM_RBUTTONUP
        elif lparam == win32.WM_RBUTTONUP:
            log.info("Клик по иконке (правая кнопка)")
        return original(wparam, lparam)

    icon._on_notify = patched
    icon._message_handlers[win32.WM_NOTIFY] = patched
    log.info("Обработчик клика по иконке установлен")


def enable_dpi_awareness():
    """Без этого на экранах с масштабом отличным от 100% текст размыт."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def setup_logging():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
    )


def main():
    setup_logging()
    enable_dpi_awareness()
    if already_running():
        log.info("Приложение уже запущено — показываю панель")
        request_show_panel()
        return
    log.info("Запуск %s", APP_NAME)
    App().run()
    log.info("Выход")


if __name__ == "__main__":
    main()
