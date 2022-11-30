import asyncio
import sys
import curses
import signal
import collections
import abc

class InputManager:
    def __init__(self, window: curses.window, queue: asyncio.Queue | None = None) -> None:
        self._window = window
        self._input_queue = queue if queue else asyncio.Queue()
        self._input = collections.deque()
        self._cursor = 0

    async def manage_input(self):
        self._window.nodelay(True)
        self._window.keypad(True)

        loop = asyncio.get_event_loop()
        event = asyncio.Event()
        loop.add_reader(sys.stdin, event.set)

        while True:
            await event.wait()
            event.clear()

            try:
                c = self._window.get_wch()
            except curses.error:
                continue

            self._input_queue.put_nowait(c)

    async def get(self):
        return await self._input_queue.get()

class EditField:
    def __init__(self, max_chars=0) -> None:
        self._text = collections.deque()
        self._cursor = 0
        self._max_chars = max_chars

    def keypress(self, ch):
        if self._isspecial(ch):
            ch = ord(ch)

        if isinstance(ch, str):
            return self._insert(ch)

        elif self._isbackspace(ch):
            return self._backspace()

        elif self._isctrlbackspace(ch):
            while self._cur_char() == ' ':
                self._backspace()
            while self._cur_char() not in (' ', None):
                self._backspace()
            return 'input', None

        elif self._isdelete(ch):
            return self._delete()

        elif self._isctrldelete(ch):
            while self._cur_char() == ' ':
                self._delete()
            while self._cur_char() not in (' ', None):
                self._delete()
            return 'input', None

        elif ch == curses.KEY_RIGHT:
            if self._cursor < len(self._text):
                return self._move_cursor(+1)

        elif ch == curses.KEY_LEFT:
            if self._cursor > 0:
                return self._move_cursor(-1)

        elif ch == curses.KEY_HOME:
            return self._move_cursor(0, relative=False)

        elif ch == curses.KEY_END:
            return self._move_cursor(len(self._text), relative=False)

        elif self._isenter(ch):
            return 'flush', self._flush()

        else:
            return 'special', ch

    def _isspecial(self, ch) -> bool:
        if isinstance(ch, str):
            code = ord(ch)
            return code < 32 or code == 127
        else:
            return False

    def _insert(self, ch) -> None:
        if self._max_chars == 0 or len(self._text) < self._max_chars:
            self._text.insert(self._cursor, ch)
            self._move_cursor(+1)
            return 'input', None

    def _move_cursor(self, new_pos: int, *, relative: bool = True):
        if relative:
            new_cursor = self.cursor + new_pos
        else:
            new_cursor = new_pos

        if new_cursor >= 0 and new_cursor <= len(self._text):
            self._cursor = new_cursor
        return 'cursor', None

    def _isbackspace(self, ch) -> bool:
        return ch in (curses.KEY_BACKSPACE, 127)

    def _isctrlbackspace(self, ch) -> bool:
        return ch in (23, 263, 8)

    def _isdelete(self, ch) -> bool:
        return ch == curses.KEY_DC

    def _isctrldelete(self, ch) -> bool:
        return False

    def _isenter(self, ch):
        return ch in (curses.KEY_ENTER, 10)

    def _backspace(self):
        if self._cursor > 0:
            self._move_cursor(-1)
            self._delete()
        return 'input', None

    def _delete(self):
        if self._cursor < len(self._text):
            del self._text[self._cursor]
        return 'input', None

    def _flush(self):
        text = self.text
        self._text = collections.deque()
        self._cursor = 0

        return text

    def _cur_char(self):
        if self._cursor > 0:
            return self._text[self._cursor-1]
        else:
            return None

    @property
    def text(self):
        return ''.join(self._text)

    @property
    def cursor(self):
        return self._cursor

class Widget(abc.ABC):
    def __init__(self, x, y, width, height, name: str) -> None:
        super().__init__()
        self._name = name
        self._x = x
        self._y = y
        self._width = width
        self._height = height
        self._refresh_marker = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def x(self) -> int:
        return self._x

    @property
    def y(self) -> int:
        return self._y

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def geometry(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.width, self.height

    @abc.abstractmethod
    def set_geometry(self, geo: tuple[int, int, int, int]) -> None:
        self._x = geo[0]
        self._y = geo[1]
        self._width = geo[2]
        self._height = geo[3]

    @abc.abstractmethod
    def input(self, ch) -> bool: ...

    @abc.abstractmethod
    def refresh(self) -> None: ...

    def _set_refresh_marker(self, marker):
        self._refresh_marker = marker

    def _mark_refresh(self):
        if self._refresh_marker:
            self._refresh_marker()

    def focus(self): ...

class TextBox(Widget):
    __counter = 0

    def __init__(self, x, y, width, height, lines, *, scrollbar=True, name: str | None=None) -> None:
        if not name:
            name = f'TextBox{TextBox.__counter}'
        TextBox.__counter += 1

        super().__init__(x, y, width, height, name)

        self._text = ''
        self._text_lines = 0
        self._outer = curses.newwin(1, 1, 0, 0)
        self._inner = curses.newpad(1, 1)
        self._inner.scrollok(True)
        self._scrollbar_size = 3 if scrollbar else 0

        self._lines = lines
        self._refresh_text = False
        self._bottom = True
        self._pad_line = 0

        self.set_geometry(self.geometry)

    def input(self, ch) -> bool:
        return False

    def _count_text_lines(self) -> int:
        lines, col = 1, 0
        for c in self._text:
            if c == '\n':
                col = 0
                lines += 1
            else:
                col += 1

            if col >= self._pad_width:
                col = 0
                lines += 1

        return lines

    @property
    def text(self) -> str:
        return self._text

    @text.setter
    def text(self, text) -> None:
        self._text = text
        self._text_lines = self._count_text_lines()
        self._refresh_text = True
        self._mark_refresh()

    def refresh(self) -> None:
        self._outer.erase()
        self._outer.box()
        self._outer.noutrefresh()

        if self._refresh_text:
            self._inner.erase()
            self._inner.addstr(self._text)

        if self._bottom:
            pad_row = max(0, self._text_lines-self._pad_height)
            pad_row = min(pad_row, self._lines-self._pad_height)
        else:
            pad_row = self._pad_line

        self._inner.noutrefresh(pad_row, 0, self.y+1, self.x+1, self.y+self._pad_height, self.x+self._pad_width)

    def set_geometry(self, geo: tuple[int, int, int, int]):
        super().set_geometry(geo)

        self._pad_width = self.width-self._scrollbar_size-2
        self._pad_height = self.height-2
        self.text = self.text

        try:
            self._outer.mvwin(self.y, self.x)
        except curses.error:
            raise Exception(f'{self.y=} {self.x=}')
        self._outer.resize(self.height, self.width)
        self._inner.resize(self._lines, self._pad_width)
        self._mark_refresh()

class InputBox(TextBox):
    __counter = 0

    def __init__(self, x, y, width, *, name: str | None = None) -> None:
        if not name:
            name = f'InputBox{InputBox.__counter}'
        InputBox.__counter += 1

        super().__init__(x, y, width, 3, 1, scrollbar=False, name=name)
        self._edit = EditField(200)
        self._on_flush = None

    def input(self, ch) -> bool:
        ev, arg = self._edit.keypress(ch)

        if ev == 'flush':
            self.text = ''
            if self._on_flush:
                self._on_flush(arg)
            return True

        if ev == 'input':
            self.text = self._edit.text
            return True

    def focus(self):
        self._outer.move(1, self._edit.cursor + 1)

    def flush(self, f):
        self._on_flush = f

class App:
    def __init__(self) -> None:
        self._inp_win = curses.newwin(1, 1, 0, 0)
        self._input_manager = InputManager(self._inp_win)
        self._widgets: dict[str, tuple[int, Widget]] = {}
        self._focused_input: str | None = None
        self._unhandled = asyncio.Queue()
        self._refresh_ev = asyncio.Event()
        self._refresh_ev.set()
        self._on_resize = None

    def unfocus_input(self):
        self._focused_input = None

    def focus_input(self, widget: str | Widget):
        if isinstance(widget, Widget):
            widget = widget.name

        if widget in self._widgets:
            self._focused_input = widget
        else:
            raise ValueError(f'Invalid widget {widget}')

    def add_widget(self, widget: Widget, *, z: int):
        widget._set_refresh_marker(self._refresh_ev.set)
        self._widgets[widget.name] = z, widget

    def _add_unhandled_input(self, ch):
        self._unhandled.put_nowait(ch)

    def _get_widget(self, widget: str) -> Widget:
        return self._widgets[widget][1]

    async def _feed_input(self):
        while True:
            ch = await self._input_manager.get()
            if ch == curses.KEY_RESIZE and self._on_resize:
                curses.update_lines_cols()
                self._on_resize(curses.LINES, curses.COLS)

            if self._focused_input:
                unhandled = self._get_widget(self._focused_input).input(ch)
                if unhandled:
                    self._add_unhandled_input(ch)

    async def _refresher(self):
        while True:
            await self._refresh_ev.wait()
            self._refresh_ev.clear()
            self._refresh()

    def _refresh(self):
        widgets = self._widgets.values()
        ordered_widgets = sorted(widgets, key=lambda k: k[0])
        for z, widget in ordered_widgets:
            widget.refresh()

        curses.doupdate()
        if self._focused_input:
            self._get_widget(self._focused_input).focus()

    async def run(self):
        def sig_resize(*args):
            curses.update_lines_cols()
            if self._on_resize:
                self._on_resize(curses.LINES, curses.COLS)

        # signal.signal(signal.SIGWINCH, sig_resize)

        coros = [
            self._input_manager.manage_input(),
            self._feed_input(),
            self._refresher()
        ]

        tasks = [asyncio.create_task(coro) for coro in coros]

        await asyncio.wait(tasks)

    def resize(self, f):
        self._on_resize = f

async def main(stdscr: curses.window):
    curses.set_escdelay(25)
    app = App()

    lines, cols = curses.LINES, curses.COLS

    userlen = min(30, cols//3)
    textbox = TextBox(0, 0, cols - userlen, lines-3, 1000)
    userlist = TextBox(cols-userlen, 0, userlen, lines-3, 100)
    inputbox = InputBox(0, lines-3, cols)

    app.add_widget(textbox, z=1)
    app.add_widget(userlist, z=2)
    app.add_widget(inputbox, z=3)
    app.focus_input(inputbox)

    @inputbox.flush
    def flush(line):
        if line:
            textbox.text += f'{line}\n'

    @app.resize
    def resize(lines, cols):
        userlen = min(30, cols//3)
        textbox.set_geometry((0, 0, cols-userlen, lines-3))
        inputbox.set_geometry((0, lines-3, cols, 3))
        userlist.set_geometry((cols-userlen, 0, userlen, lines-3))

    await app.run()

def sync_main(stdscr: curses.window):
    return asyncio.run(main(stdscr))

if __name__ == '__main__':
    curses.wrapper(sync_main)
