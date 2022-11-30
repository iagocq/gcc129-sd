import os
import asyncio
import sys
import curses
import signal
import collections
import abc
import logging
import enum
import traceback

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
        self._mark_refresh()

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
        def refresh():
            self._refresh_ev.set()

        widget._set_refresh_marker(refresh)
        self._widgets[widget.name] = z, widget

    def _add_unhandled_input(self, ch):
        self._unhandled.put_nowait(ch)

    def _get_widget(self, widget: str) -> Widget:
        return self._widgets[widget][1]

    async def _feed_input(self):
        while True:
            ch = await self._input_manager.get()

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
        loop = asyncio.get_event_loop()

        def sig_resize(*args):
            def loop_resize():
                size = os.get_terminal_size()
                lines, cols = size.lines, size.columns
                curses.resizeterm(lines, cols)

                if self._on_resize:
                    self._on_resize(lines, cols)
            loop.call_soon_threadsafe(loop_resize)

        signal.signal(signal.SIGWINCH, sig_resize)

        coros = [
            self._input_manager.manage_input(),
            self._refresher(),
            self._feed_input(),
        ]

        tasks = [asyncio.create_task(coro) for coro in coros]

        await asyncio.wait(tasks)

    def resize(self, f):
        self._on_resize = f

def _split_irc_command(command: bytes):
        if command[-2:] != b'\r\n' or len(command) < 3 or len(command) > 512:
            return False

        command = command[:-2]

        prefix = None
        idx = -1
        parts = []
        if command[0] == ord(':'):
            prefixend = command.find(b' ')
            prefix = command[1:prefixend].decode()
            idx = prefixend

        parts = []
        while idx < len(command)-1:
            if len(parts) == 15:
                return False

            if command[idx+1] == ord(':'):
                trailing = command[idx+2:]
                parts.append(trailing)
                idx = len(command)
                break
            else:
                next = command.find(b' ', idx+1)
                if next - idx == 1:
                    return False

                if next != -1:
                    part = command[idx+1:next]
                else:
                    part = command[idx+1:]

                parts.append(part)

                idx = next

            if idx == -1:
                break

        if not parts:
            return False

        parts = [p.decode() for p in parts]
        cmd = parts[0]

        return prefix, cmd, parts[1:]

def _make_irc_command(cmd: str, *args: str, prefix=None):
    command = bytearray()
    if prefix:
        command.extend(b':')
        command.extend(prefix.encode())
        command.extend(b' ')

    command.extend(cmd.encode())
    for i, arg in enumerate(args):
        if arg:
            command.extend(b' ')
            if i == len(args)-1 and ' ' in arg:
                command.extend(b':')
            command.extend(arg.encode())

    command.extend(b'\r\n')
    return bytes(command)

class Reply(enum.Enum):
    WELCOME = '001', 'oi oi!'
    ALREADYREGISTRED = 462, 'Comando não autorizado (já registrado)'
    NEEDMOREPARAMS = 461, 'Faltam parâmetros'
    LIST = 322, ''
    LISTEND = 323, 'Fim de LIST'
    NOSUCHNICK = 401, 'Canal/nick inexistente'
    NOTOPIC = 331, 'Sem tópico'
    NAMREPLY = 353, ''
    ENDOFNAMES = 366, 'Fim da lista de NAMES'

class IRCUser:
    def __init__(self, server: str, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._registered = False
        self._nick = None
        self._username = None
        self._realname = None
        self._mode = 0
        self._reader = reader
        self._writer = writer
        self._address = reader._transport.get_extra_info('peername')[0]
        self._server = '127.0.0.1'

    @property
    def registered(self):
        return self._registered

    @property
    def nick(self):
        return self._nick

    @property
    def realname(self):
        return self._realname

    @property
    def username(self):
        return self._username

    @property
    def visible(self):
        return True

    async def recv(self):
        data = await self._reader.readuntil(b'\r\n')
        return data

    async def send(self, command):
        if debug:
            print(command)
        self._writer.write(command)

    async def reply(self, reply: Reply, *args, prefix='server'):
        t = reply.value
        if prefix == 'server':
            prefix = self._server
        elif prefix == 'user':
            prefix = self.prefix
        else:
            prefix = None
        command = _make_irc_command(str(t[0]), *args, t[1], prefix=prefix)
        await self.send(command)

    @property
    def prefix(self):
        return f'{self.nick}!{self.username}@{self._address}'

class IRCChannel:
    def __init__(self) -> None:
        self._users: set[IRCUser] = set()
        self._topic: str = ''

    @property
    def n_of_users(self):
        n = 0
        for u in self._users:
            if u.visible:
                n += 1
        return n

    @property
    def topic(self):
        return self._topic

    async def broadcast(self, command, exclude=None):
        tasks = [asyncio.create_task(u.send(command)) for u in self._users if u is not exclude]
        if tasks:
            await asyncio.wait(tasks, timeout=1)

class IRCServer:
    def __init__(self, port=6665, password: str | None=None) -> None:
        self._channels: dict[str, IRCChannel] = {}
        self._nicks: dict[str, IRCUser] = {}
        self._users: dict[str, IRCUser] = {}
        self._events = asyncio.Queue()
        self._port = port
        self._password = password

    async def _handle_user(self, user: IRCUser):
        while True:
            command = await user.recv()
            print(command)

            try:
                sp = _split_irc_command(command)
                if not sp: continue
            except UnicodeDecodeError:
                continue

            prefix, cmd, parts = sp
            cmd = cmd.upper()

            if prefix:
                continue

            if cmd == 'NICK':
                if user.nick in self._nicks:
                    del self._nicks[user.nick]
                user._nick = parts[0]
                self._nicks[user.nick] = user

            elif cmd == 'USER':
                if user.registered:
                    await user.reply(Reply.ALREADYREGISTRED)
                elif len(parts) < 4:
                    await user.reply(Reply.NEEDMOREPARAMS, 'USER')
                else:
                    user._username = parts[0]
                    try:
                        user._mode = int(parts[1])
                    except ValueError: pass

                    user._realname = parts[3]
                    user._registered = True

                    self._users[user.username] = user.username
                    await user.reply(Reply.WELCOME, user.username)

            elif cmd == 'QUIT':
                msg = '' if not parts else parts[0]
                await self._quit(user, msg)

            elif cmd == 'JOIN':
                if not parts:
                    await user.reply(Reply.NEEDMOREPARAMS, 'JOIN')
                elif parts[0] == '0':
                    for channel in self._channels:
                        await self._part(user, channel)
                else:
                    for channel in parts[0].split(','):
                        await self._join(user, channel)
            elif cmd == 'PART':
                if not parts:
                    await user.reply(Reply.NEEDMOREPARAMS, 'PART')
                else:
                    for channel in parts[0].split(','):
                        await self._part(user, channel)

            elif cmd == 'LIST':
                channels = parts[0].split(',') if parts else self._channels.keys()
                for chname in channels:
                    if chname in self._channels:
                        channel = self._channels[chname]
                        await user.reply(Reply.LIST, chname, f'{channel.n_of_users}', channel.topic)

                await user.reply(Reply.LISTEND)

            elif cmd == 'PRIVMSG':
                if len(parts) < 2:
                    await user.reply(Reply.NEEDMOREPARAMS, 'PRIVMSG')
                else:
                    await self._privmsg(user, parts[0], parts[1])

    async def handle_user(self, user: IRCUser):
        try:
            await self._handle_user(user)
        except:
            traceback.print_exc()

    async def run(self):
        def connect(reader, writer):
            client = IRCUser('aaaaaaaaaaaa', reader, writer)
            asyncio.run_coroutine_threadsafe(self.handle_user(client), loop=asyncio.get_event_loop())

        server = await asyncio.start_server(connect, port=self._port)
        await server.serve_forever()

    async def _join(self, user: IRCUser, chname):
        if chname not in self._channels:
            self._channels[chname] = IRCChannel()

        channel = self._channels[chname]
        channel._users.add(user)

        await channel.broadcast(_make_irc_command('JOIN', chname, prefix=user.prefix))
        await user.reply(Reply.NOTOPIC, chname)
        await user.reply(Reply.NAMREPLY)

    async def _privmsg(self, user: IRCUser, chname: str, text: str):
        cmd = _make_irc_command('PRIVMSG', chname, text, prefix=user.prefix)
        if chname[0] in '#&+':
            if chname not in self._channels:
                await user.reply(Reply.NOSUCHNICK, chname)
            else:
                await self._channels[chname].broadcast(cmd, exclude=user)
        else:
            if chname not in self._users:
                await user.reply(Reply.NOSUCHNICK, chname)
            else:
                await self._users[chname].send(cmd)

class IRCClient:
    def __init__(self, channel='#t') -> None:
        self._other_users: list[str, set[str]] = []
        self._reader = None
        self._writer = None
        self._on_message = None
        self._channel = channel
        self._login_ev = asyncio.Event()
        self._user = None
        self._nick = ''
        self._msg_queue = asyncio.Queue()

    async def connect(self, ip, port):
        reader, writer = await asyncio.open_connection(ip, port)
        self._reader = reader
        self._writer = writer
        self._user = IRCUser('', self._reader, self._writer)

    async def send_command(self, command):
        self._writer.send(command)

    async def run(self):
        tasks = [asyncio.create_task(t) for t in [
            self._wait_login(),
            self._send_forever()
        ]]

        while True:
            p = await self._user.recv()
            prefix, cmd, args = _split_irc_command(p)
            nick = prefix.split('!')[0]
            if cmd == 'PRIVMSG':
                self._post_message(f'<{nick}> {args[1]}\n')
            if cmd == 'JOIN':
                self._post_message(f'*{nick} entrou no chat*\n')
            if cmd == 'PART':
                self._post_message(f'*{nick} saiu do chat*\n')

    def enqueue_msg(self, msg):
        self._msg_queue.put_nowait(msg)

    async def _send_forever(self):
        while True:
            msg = await self._msg_queue.get()
            await self.send_msg(msg)

    async def send_msg(self, msg):
        self._writer.write(_make_irc_command('PRIVMSG', self._channel, msg))

    async def _wait_login(self):
        await self._login_ev.wait()
        await self._user.send(_make_irc_command('NICK', self._nick))
        await self._user.send(_make_irc_command('USER', self._nick, '0', '*', self._nick))
        await self._user.send(_make_irc_command('JOIN', self._channel))

    def on_message(self, f):
        self._on_message = f

    def _post_message(self, msg):
        if self._on_message:
            self._on_message(msg)

async def main(stdscr: curses.window):
    curses.set_escdelay(25)
    app = App()
    client = IRCClient()

    lines, cols = curses.LINES, curses.COLS

    textbox = TextBox(0, 0, cols, lines-3, 1000)
    inputbox = InputBox(0, lines-3, cols)

    app.add_widget(textbox, z=1)
    app.add_widget(inputbox, z=3)
    app.focus_input(inputbox)

    nick = None
    @inputbox.flush
    def flush(line):
        nonlocal nick
        if line:
            if not nick:
                nick = line[:9]
                client._nick = nick
                client._login_ev.set()
            else:
                textbox.text += f'<{nick}> {line}\n'
                client.enqueue_msg(line)

    @client.on_message
    def on_msg(msg):
        textbox.text += msg

    @app.resize
    def resize(lines, cols):
        textbox.set_geometry((0, 0, cols, lines-3))
        inputbox.set_geometry((0, lines-3, cols, 3))

    await client.connect(sys.argv[1], sys.argv[2])

    await asyncio.wait([asyncio.create_task(t) for t in [
        client.run(),
        app.run()
    ]])

def sync_main(stdscr: curses.window):
    return asyncio.run(main(stdscr))

debug = False
async def serve_main():
    global debug
    debug = True
    server = IRCServer()
    await server.run()

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'serve':
        asyncio.run(serve_main())
    else:
        curses.wrapper(sync_main)
