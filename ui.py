"""Окна TextHelper: результат «БЫЛО → СТАЛО» и простые сообщения."""

import re
import tkinter as tk
from tkinter import font as tkfont

from PIL import Image, ImageDraw, ImageTk

import speech

# Единая синяя палитра: фон, поверхности, белый текст.
BG = "#1B4FE0"          # фон окон
BG_WAS = "#1743C4"      # поле «БЫЛО» — темнее фона
BORDER = "#4B7BF5"
BORDER_OK = "#8FD8FF"   # рамка поля «СТАЛО»
ACCENT = "#FFFFFF"      # главная кнопка: белая на синем
ACCENT_DARK = "#E3EBFF"
MUTED = "#C6D6FF"       # подписи
TEXT = "#FFFFFF"
ERROR = "#FFC2C2"

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 8)
FONT_TITLE = ("Segoe UI Semibold", 12)

# Кнопки действий на панели: светло-фиолетовые карточки с рукописной надписью.
CARD = "#3568F0"
CARD_HOVER = "#4C7DFF"
CARD_TEXT = "#FFFFFF"
CARD_SHADOW = "#3568F0"  # совпадает с фоном карточки: обводка не видна
PANEL_TEXT = "#D5E0FF"
FONT_CARD = ("Segoe UI", 10)

# Tk на Windows рисует эмодзи неровно, поэтому в окнах показываем текст без них.
# В меню трея эмодзи остаются: там их рисует сама Windows.
_EMOJI = re.compile(
    "[←-➿⬀-⯿️\U0001f000-\U0001faff]"
)


def plain(text):
    """Убирает эмодзи из строки для показа в окне."""
    return _EMOJI.sub("", text).strip()


def _icon(painter, size=15, scale=4):
    """Рисует значок в увеличенном размере и уменьшает — так края мягче."""
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    painter(ImageDraw.Draw(image), big, scale)
    return ImageTk.PhotoImage(image.resize((size, size), Image.LANCZOS))


def mic_icon(color=TEXT):
    """Микрофон: капсула на ножке с подставкой."""
    def paint(draw, big, scale):
        draw.rounded_rectangle(
            [big * 0.36, big * 0.10, big * 0.64, big * 0.56],
            radius=big * 0.14, fill=color,
        )
        draw.arc(
            [big * 0.22, big * 0.34, big * 0.78, big * 0.80],
            start=0, end=180, fill=color, width=scale * 2,
        )
        draw.line([(big * 0.5, big * 0.78), (big * 0.5, big * 0.90)],
                  fill=color, width=scale * 2)
        draw.line([(big * 0.32, big * 0.90), (big * 0.68, big * 0.90)],
                  fill=color, width=scale * 2)

    return _icon(paint)


def copy_icon(page_color, color=TEXT):
    """Две странички одна на другой."""
    def paint(draw, big, scale):
        draw.rounded_rectangle(
            [big * 0.04, big * 0.04, big * 0.56, big * 0.70],
            radius=big * 0.08, outline=color, width=scale * 2,
        )
        # Передняя страница залита цветом кнопки, чтобы перекрыть заднюю,
        # и отодвинута — иначе на мелком размере странички сливаются.
        draw.rounded_rectangle(
            [big * 0.40, big * 0.30, big * 0.96, big * 0.96],
            radius=big * 0.08, fill=page_color, outline=color, width=scale * 2,
        )

    return _icon(paint, size=16)


class RoundedButton(tk.Canvas):
    """Кнопка со скруглёнными углами.

    Обычная кнопка Windows углы скруглять не умеет, поэтому рисуем сами
    на холсте: скруглённый прямоугольник плюс надпись.
    """

    def __init__(self, master, text, command, width=250, height=38,
                 radius=13, fill=None, hover=None, fg=None,
                 font=None, page=None):
        # Цвета берём в момент создания, а не при загрузке модуля,
        # иначе тему не поменять.
        fill = fill or CARD
        hover = hover or CARD_HOVER
        fg = fg or CARD_TEXT
        font = font or FONT_CARD
        page = page or BG

        super().__init__(
            master, width=width, height=height, bg=page,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self._command = command
        self._fill = fill
        self._hover = hover

        self._shape = self._rounded(2, 2, width - 2, height - 2, radius, fill)
        self._label = self.create_text(
            15, height // 2, text=text, anchor="w", fill=fg, font=font,
        )

        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda _e: self.itemconfigure(self._shape, fill=self._hover))
        self.bind("<Leave>", lambda _e: self.itemconfigure(self._shape, fill=self._fill))

    def _rounded(self, x1, y1, x2, y2, r, fill):
        points = [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, fill=fill, outline="")

    def _click(self, _event):
        if self._command:
            self._command()


def _center(window):
    window.update_idletasks()
    width = window.winfo_width()
    height = window.winfo_height()
    x = (window.winfo_screenwidth() - width) // 2
    y = (window.winfo_screenheight() - height) // 3
    window.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def _raise(window, stay_on_top=False):
    window.attributes("-topmost", True)
    window.lift()
    window.focus_force()
    if not stay_on_top:
        window.after(400, lambda: window.attributes("-topmost", False))


def _pin_top_right(window, margin=24, top=90):
    """Прижимает окно к правому верхнему углу и держит поверх всех окон.

    Так панель остаётся на виду, пока пользователь работает в Word или браузере,
    и не закрывает середину документа.
    """
    def place(attempt=0):
        window.update_idletasks()
        width = window.winfo_width()
        # Пока окно не показано, ширина ещё не настоящая — ждём и пробуем снова.
        if width <= 1 and attempt < 20:
            window.after(30, lambda: place(attempt + 1))
            return
        x = max(window.winfo_screenwidth() - width - margin, 0)
        window.geometry(f"+{x}+{top}")

    window.attributes("-topmost", True)
    window.lift()
    place()
    # Windows может переставить окно уже после показа — закрепляем ещё раз.
    window.after(150, place)


class ResultWindow(tk.Toplevel):
    """Окно «БЫЛО → СТАЛО» с полем доработки."""

    def __init__(self, master, action_title, original, on_refine):
        super().__init__(master)
        self._on_refine = on_refine
        self._result = ""
        self._dictation = None

        self.title(f"TextHelper — {plain(action_title)}")
        self.configure(bg=BG, padx=14, pady=11)
        self.resizable(False, False)
        self.bind("<Escape>", lambda _event: self.destroy())

        tk.Label(
            self, text=plain(action_title), font=FONT_TITLE, bg=BG, fg=TEXT,
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        self._was = self._add_text_block("БЫЛО", height=4, bg=BG_WAS, border=BORDER)
        self._was.insert("1.0", original)
        self._was.configure(state="disabled")

        self._became = self._add_text_block(
            "СТАЛО", height=8, bg=CARD, border=BORDER_OK
        )

        self._status = tk.Label(
            self, text="", font=FONT_SMALL, bg=BG, fg=MUTED, anchor="w"
        )
        self._status.pack(fill="x", pady=(2, 6))

        self._build_refine_row()
        self._build_copy_button()

        # Держим поверх документа: иначе окно уходит за Word, стоит туда кликнуть.
        _center(self)
        _raise(self, stay_on_top=True)

    # --- построение интерфейса -------------------------------------------

    def _add_text_block(self, caption, height, bg, border):
        tk.Label(
            self, text=caption, font=FONT_SMALL, bg=BG, fg=MUTED, anchor="w"
        ).pack(fill="x")

        frame = tk.Frame(self, bg=border, padx=1, pady=1)
        frame.pack(fill="x", pady=(2, 8))

        widget = tk.Text(
            frame, height=height, width=58, font=FONT, bg=bg, fg=TEXT,
            wrap="word", relief="flat", padx=8, pady=6,
            insertbackground=TEXT,
        )
        widget.pack(fill="both", expand=True)
        return widget

    def _build_refine_row(self):
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x")

        entry_frame = tk.Frame(row, bg=BORDER, padx=1, pady=1)
        entry_frame.pack(side="left", fill="x", expand=True)

        self._entry = tk.Entry(
            entry_frame, font=FONT, bg=CARD, fg=TEXT, relief="flat",
            insertbackground=TEXT,
        )
        self._entry.pack(fill="x", ipady=6, ipadx=6)
        self._entry.bind("<Return>", lambda _event: self._refine())
        self._add_placeholder(self._entry, "сделай ещё короче")

        self._mic_image = mic_icon()
        self._mic = tk.Button(
            row, text=" Голос", font=FONT, bg=CARD, fg=TEXT,
            image=self._mic_image, compound="left",
            activebackground=CARD_HOVER, relief="flat", padx=12, pady=5,
            # Иначе Windows красит выключенную кнопку серым, и на синем
            # фоне она выглядит погасшей.
            disabledforeground=TEXT,
            cursor="hand2", command=self._toggle_mic,
        )
        self._mic.pack(side="left", padx=(8, 0))

        self._send = tk.Button(
            row, text="Отправить", font=FONT, bg=ACCENT, fg=BG,
            activebackground=ACCENT_DARK, activeforeground=BG,
            relief="flat", padx=16, pady=5, cursor="hand2",
            command=self._refine,
        )
        self._send.pack(side="left", padx=(8, 0))

        tk.Label(
            self, text="«Голос» — сказать уточнение вслух", font=FONT_SMALL,
            bg=BG, fg=MUTED, anchor="w",
        ).pack(fill="x", pady=(4, 10))

    def _build_copy_button(self):
        self._copy_image = copy_icon(CARD)
        self._copy = tk.Button(
            self, text="  Скопировать", font=FONT, bg=CARD, fg=TEXT,
            image=self._copy_image, compound="left",
            activebackground=CARD_HOVER, relief="flat", pady=7, cursor="hand2",
            disabledforeground=TEXT,
            command=self._copy_result,
        )
        self._copy.pack(fill="x")

    def _add_placeholder(self, entry, text):
        entry.insert(0, text)
        entry.configure(fg=MUTED)

        def on_focus_in(_event):
            if entry.get() == text and entry.cget("fg") == MUTED:
                entry.delete(0, "end")
                entry.configure(fg=TEXT)

        def on_focus_out(_event):
            if not entry.get():
                entry.insert(0, text)
                entry.configure(fg=MUTED)

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        self._placeholder = text

    # --- состояния --------------------------------------------------------

    def set_busy(self, message="Обрабатываю…"):
        self._status.configure(text=message, fg=MUTED)
        self._send.configure(state="disabled")
        self._entry.configure(state="disabled")
        self._copy.configure(state="disabled")
        self._mic.configure(state="disabled")

    def set_result(self, text):
        self._result = text
        self._became.configure(state="normal")
        self._became.delete("1.0", "end")
        self._became.insert("1.0", text)
        self._status.configure(text="")
        self._send.configure(state="normal")
        self._entry.configure(state="normal")
        self._mic.configure(state="normal")
        self._copy.configure(state="normal", text="  Скопировать")

    def set_error(self, message):
        self._status.configure(text=message, fg=ERROR)
        self._send.configure(state="normal")
        self._entry.configure(state="normal")
        self._mic.configure(state="normal")
        self._copy.configure(state="normal")

    # --- действия ---------------------------------------------------------

    def _toggle_mic(self):
        """Первое нажатие — запись, второе — распознавание сказанного."""
        if self._dictation is not None:
            self._dictation.stop()
            self._mic.configure(text="…", state="disabled", bg=CARD, fg=TEXT)
            self._status.configure(text="Распознаю…", fg=ACCENT)
            return

        if not speech.available():
            self._status.configure(
                text="Модель речи не установлена: python install_speech.py",
                fg=ERROR,
            )
            return

        self._dictation = speech.Dictation()
        self._dictation.start()
        self._mic.configure(text="Стоп", bg=ERROR, fg="white")
        self._status.configure(
            text="Идёт запись. Говорите, потом нажмите «Стоп»", fg=ERROR
        )
        self.after(200, self._poll_dictation)

    def _poll_dictation(self):
        dictation = self._dictation
        if dictation is None:
            return
        if not dictation.done:
            if dictation.recording:
                self._status.configure(text=self._meter(dictation), fg=ERROR)
            self.after(120, self._poll_dictation)
            return

        self._dictation = None
        self._mic.configure(text=" Голос", bg=CARD, fg=TEXT, state="normal")

        if dictation.error:
            self._status.configure(text=dictation.error, fg=ERROR)
        elif dictation.text:
            self._put_into_entry(dictation.text)
            self._status.configure(text="")
        else:
            self._status.configure(text="Ничего не расслышала", fg=MUTED)

    @staticmethod
    def _meter(dictation):
        """Полоска громкости и таймер: видно, что микрофон вас слышит."""
        filled = min(12, int(dictation.level * 40))
        bar = "█" * filled + "░" * (12 - filled)
        return f"Слышу вас  {bar}  {dictation.seconds:.0f} с — жмите «Стоп»"

    def _put_into_entry(self, text):
        """Дописывает распознанное в поле уточнения."""
        self._entry.configure(state="normal")
        if self._entry.get() == self._placeholder and self._entry.cget("fg") == MUTED:
            self._entry.delete(0, "end")
        self._entry.configure(fg=TEXT)
        if self._entry.get().strip():
            self._entry.insert("end", " ")
        self._entry.insert("end", text)
        self._entry.focus_set()

    def _refine(self):
        note = self._entry.get().strip()
        if not note or note == self._placeholder:
            return
        self._entry.configure(state="normal")
        self._entry.delete(0, "end")
        self._on_refine(self, note)

    def _copy_result(self):
        if not self._result:
            return
        self.clipboard_clear()
        self.clipboard_append(self._result)
        self.update()
        self._copy.configure(text="  Скопировано ✓")
        self.after(1500, lambda: self._copy.configure(text="  Скопировать"))

    @property
    def result(self):
        return self._result


class ActionsWindow(tk.Toplevel):
    """Список действий. Дублирует меню трея и открывается при запуске."""

    def __init__(self, master, actions, on_pick, on_close, on_quit):
        super().__init__(master)
        self._on_pick = on_pick

        self.title("TextHelper")
        self.configure(bg=BG, padx=10, pady=8)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", on_close)

        tk.Label(
            self, text="Выделите текст — и жмите",
            font=FONT_SMALL, bg=BG, fg=PANEL_TEXT, anchor="w",
        ).pack(fill="x", pady=(0, 8))

        # Ширина кнопок — по самой длинной надписи, чтобы текст не обрезался.
        measure = tkfont.Font(font=FONT_CARD)
        width = max(measure.measure(plain(a["title"])) for a in actions) + 36

        for action in actions:
            RoundedButton(
                self, text=plain(action["title"]), width=width,
                command=lambda a=action: self._pick(a),
            ).pack(fill="x", pady=2)

        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", pady=(8, 5))

        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x")

        tk.Button(
            footer, text="Выход", font=FONT_SMALL, bg=BG, fg=PANEL_TEXT,
            activebackground=CARD, relief="flat", cursor="hand2", padx=6,
            command=on_quit,
        ).pack(side="right")

        # Подпись внизу постоянно не нужна: появляется только на время сообщения.
        self._hint = tk.Label(
            self, text="", font=FONT_SMALL, bg=BG, fg=ACCENT, anchor="w",
        )

        _pin_top_right(self)

    def flash(self, message):
        """Короткое сообщение внизу панели — например, что текст не выделен."""
        self._hint.configure(text=message)
        self._hint.pack(fill="x", pady=(6, 0))
        self.after(2500, self._hint.pack_forget)

    def _pick(self, action):
        self._on_pick(action)


def show_message(master, title, message, kind="info"):
    """Простое окно с сообщением. Используется для ошибок и предупреждений."""
    window = tk.Toplevel(master)
    window.title(f"TextHelper — {title}")
    window.configure(bg=BG, padx=20, pady=16)
    window.resizable(False, False)
    window.bind("<Escape>", lambda _event: window.destroy())

    tk.Label(
        window, text=plain(title), font=FONT_TITLE, bg=BG,
        fg=ERROR if kind == "error" else TEXT, anchor="w",
    ).pack(fill="x", pady=(0, 8))

    tk.Label(
        window, text=message, font=FONT, bg=BG, fg=TEXT,
        wraplength=420, justify="left", anchor="w",
    ).pack(fill="x", pady=(0, 14))

    tk.Button(
        window, text="Понятно", font=FONT, bg=ACCENT, fg="white",
        activebackground=ACCENT_DARK, activeforeground="white",
        relief="flat", padx=20, pady=6, cursor="hand2",
        command=window.destroy,
    ).pack(anchor="e")

    _center(window)
    _raise(window)
    return window


def ask_yes_no(master, title, message, yes_text="Да", no_text="Отмена"):
    """Окно с вопросом. Возвращает True/False, блокирует до ответа."""
    window = tk.Toplevel(master)
    window.title(f"TextHelper — {title}")
    window.configure(bg=BG, padx=20, pady=16)
    window.resizable(False, False)

    answer = {"value": False}

    def choose(value):
        answer["value"] = value
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", lambda: choose(False))
    window.bind("<Escape>", lambda _event: choose(False))

    tk.Label(
        window, text=plain(title), font=FONT_TITLE, bg=BG, fg=TEXT, anchor="w"
    ).pack(fill="x", pady=(0, 8))

    tk.Label(
        window, text=message, font=FONT, bg=BG, fg=TEXT,
        wraplength=420, justify="left", anchor="w",
    ).pack(fill="x", pady=(0, 14))

    row = tk.Frame(window, bg=BG)
    row.pack(anchor="e")

    tk.Button(
        row, text=no_text, font=FONT, bg=CARD, fg=TEXT, relief="flat",
        padx=16, pady=6, cursor="hand2", command=lambda: choose(False),
    ).pack(side="left", padx=(0, 8))

    tk.Button(
        row, text=yes_text, font=FONT, bg=ACCENT, fg="white",
        activebackground=ACCENT_DARK, activeforeground="white",
        relief="flat", padx=16, pady=6, cursor="hand2",
        command=lambda: choose(True),
    ).pack(side="left")

    _center(window)
    _raise(window)
    window.grab_set()
    window.wait_window()
    return answer["value"]
