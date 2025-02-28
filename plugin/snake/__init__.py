from __future__ import absolute_import, print_function, unicode_literals

import vim
from contextlib import contextmanager 
from functools import wraps, partial
import os
import sys
from os.path import expanduser, exists, abspath, join, dirname
import time
import inspect
import re

__version__ = "0.15.5"


NORMAL_MODE = "n"
VISUAL_MODE = "v"
INSERT_MODE = "i"
COMMAND_MODE = "c"

BUFFER_SCRATCH = 0

NS_GLOBAL = "g"
NS_BUFFER = "b"

PYTHON_CMD = "python"
IS_PY3 = sys.version_info[0] == 3
PYTHON_CMD = "python3" if IS_PY3 else "python"

_LEADER_REGEX = re.compile(r"\\<leader>", re.I)
_BUFFER_LIST_REGEX = re.compile(r"^\s*(\d+)\s+(.+?)\s+\"(.+?)\"", re.M)

_mapped_functions = {
}

# if pyeval doesn't exist, we use our own, defined in prelude.vim.  pyeval
# doesn't exist in vim 7.3
PYEVAL = "pyeval"
if not bool(int(vim.eval("exists('*pyeval')"))):
    PYEVAL = "Pyeval"

VERSION = int(int(vim.eval("v:version")))


def _get_buffer(i):
    """ a shim for vim buffer index inconsistencies """
    # for some reason, version 7.3 indexes their vim.buffers at 0 for buffer 1.
    # version 704 has buffer 1 at index 1, even though len(vim.buffers) == 1.
    # its weird.
    if VERSION < 704:
        i -= 1
    return vim.buffers[i]

def command(cmd, capture=False):
    """ wraps vim.capture to execute a vim command.  if capture is true, we'll
    return the output of that command """
    if capture:
        with preserve_registers("a"):
            vim.command("redir @a")
            vim.command(cmd)
            vim.command("redir END")
            out = get_register("a")
    else:
        out = None
        vim.command(cmd)
    return out


def dispatch_mapped_function(key):
    """ this function will be called by any function mapped to a key in visual
    mode.  because we can't tell vim "hey, call this arbitrary, possibly
    anonymous, callable on key press", we have a single dispatch function to do
    that work for vim """
    try:
        fn = _mapped_functions[key]
    except KeyError:
        raise Exception("""unable to find mapped function with id() == %s.
            Something bad related to reloading has happened.  Typically, this is
            because you set up a key_map inside of a @when_buffer_is and
            reloaded your ~/.vim.py.  The result is that the function decorated
            by @when_buffer_is isn't re-run with updated key_mappings, so the
            key_mappings have references to old callbacks.""" % key)
    else:
        return fn()

def _generate_autocommand_name(fn):
    """ takes a function and returns a name that is unique to the function and
    where it was defined.  the name must be reproducible between startup calls
    because its for an auto command group, and we must clear the old group out
    when reloading
    
    http://learnvimscriptthehardway.stevelosh.com/chapters/14.html#clearing-groups
    """
    src = None
    try:
        src = inspect.getsourcefile(fn)
    except TypeError:
        pass

    if not src:
        src = "."
    return src + ":" + fn.__name__

def register_fn(fn):
    """ takes a function and returns a string handle that we can use to call the
    function via the "python" command in vimscript """
    fn_key = id(fn)
    _mapped_functions[fn_key] = fn
    return "snake.dispatch_mapped_function(%s)" % fn_key

@contextmanager
def preserve_cursor():
    """ persists cursor state across context. does not work in visual mode,
    because visual mode has 2 cursor locations, for start and end cursors """
    p = get_cursor_position()
    try:
        yield
    finally:
        set_cursor_position(p)

@contextmanager
def preserve_buffer():
    old_buffer = get_current_buffer()
    try:
        yield
    finally:
        set_buffer(old_buffer)

@contextmanager
def preserve_mode():
    """ prevents a change of vim mode state """
    old_mode = get_mode()
    visual_modes = ("v", "V", "^V")

    try:
        yield
    finally:
        cur_mode = get_mode()
        if cur_mode == "n":
            if old_mode in visual_modes:
                keys("gv")
        elif cur_mode in visual_modes:
            if old_mode == "n":
                keys("\<esc>")

@contextmanager
def preserve_registers(*regs):
    """ prevents a change of register state """
    old_regs = {}

    special_regs = ('0', '"')
    regs = regs
    for reg in regs:
        contents = get_register(reg)
        old_regs[reg] = contents
        clear_register(reg)

    # we can't do a clear on the special registers, because setting one will
    # wipe out the other
    for reg in special_regs:
        contents = get_register(reg)
        old_regs[reg] = contents


    try:
        yield
    finally:
        for reg in regs + special_regs:
            old_contents = old_regs[reg]
            if old_contents is None:
                clear_register(reg)
            else:
                set_register(reg, old_contents)

def debug(msg, persistent=False):
    """ prints a msg to your lower vim command area, for debugging.  if you set
    persistent=True, you can view your previous message by executing :messages """
    msg = str(msg)
    cmd = "echo"
    if persistent:
        cmd = "echom"
    command("%s '%s'" % (cmd, escape_string_sq(msg)))


def abbrev(word, expansion, local=False):
    """ creates an abbreviation in insert mode.  expansion can be a string to
    expand to or a function that returns a value to serve as the expansion """

    cmd = "iabbrev"
    if local:
        cmd = cmd + " <buffer>"

    if callable(expansion):
        fn_str = register_fn(expansion)
        expansion = "<C-r>=%s('%s')<CR>" % (PYEVAL, escape_string_sq(fn_str))

    command("%s %s %s" % (cmd, word, expansion))

def expand(stuff):
    return vim.eval("expand('%s')" % escape_string_sq(stuff))
     
def get_current_dir():
    return dirname(get_current_file())

def get_current_file():
    return expand("%:p")

def get_alternate_file():
    return expand("#:p")

def get_cur_line():
    return int(vim.eval("line('.')"))

def get_mode():
    return vim.eval("mode(1)")

def get_num_lines():
    return int(vim.eval("line('$')"))

def is_last_line():
    row, _ = get_cursor_position()
    return row == get_num_lines()


def get_cursor_position():
    #return vim.current.window.cursor
    _, start_row, start_col, _ = vim.eval("getpos('.')")
    return int(start_row), int(start_col)

def set_cursor_position(pos):
    """ set our cursor position.  pos is a tuple of (row, col) """
    full_pos = "[0, %d, %d, 0]" % (pos[0], pos[1])
    command("call setpos('.', %s)" % full_pos)


def preserve_state():
    """ a general decorator for preserving most state, including cursor, mode,
    and basic special registers " and 0 """
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # python 2.6 doesn't support this syntax:
            #with preserve_cursor(), preserve_mode(), preserve_registers():
            with preserve_mode():
                with preserve_cursor():
                    with preserve_registers():
                        return fn(*args, **kwargs)
        return wrapper
    return decorator

def escape_string_dq(s):
    s = s.replace('"', r'\"')
    return s

def escape_spaces(s):
    s = s.replace(" ", r"\ ")
    return s

def escape_string_sq(s):
    s = s.replace("'", "''")
    return s

def reselect_last_visual_selection():
    keys("gv")

def multi_let(namespace, **name_values):
    """ convenience function for setting multiple globals at once in your
    .vimrc.py, all related to a plugin.  the first argument is a namespace to be
    appended to the front of each name/value pair. """
    for name, value in name_values.items():
        let(name, value, namespace=namespace)

def _serialize_obj(obj):
    if isinstance(obj, str):
        obj = "'%s'" % escape_string_sq(obj)
    # TODO allow other kinds of serializations?
    return obj

def _compose_let_name(name, namespace, scope):
    if namespace:
        name = namespace + "_" + name
    return "%s:%s" % (scope, name)


def let(name, value, namespace=None, scope=NS_GLOBAL):
    """ sets a variable """
    value = _serialize_obj(value)
    name = _compose_let_name(name, namespace, scope)
    return command("let %s=%s" % (name, value))

let_buffer_local = partial(let, scope=NS_BUFFER)

def get(name, namespace=None, scope=NS_GLOBAL):
    """ gets a variable """
    try:
        val = vim.eval(_compose_let_name(name, namespace, scope))
    except vim.error as e:
        val = None
    return val

get_buffer_local = partial(get, scope=NS_BUFFER)

def search(s, wrap=True, backwards=False, move=True, curline=False):
    """ searches for string s, returning the (row, column) of the next match, or
    None if not found.  'move' moves the cursor to the match, 'backwards'
    specifies direction, 'wrap' for if searching should wrap around the end of
    the file """
    flags = []
    if wrap:
        flags.append("w")
    else:
        flags.append("W")

    if backwards:
        flags.append("b")

    s = escape_string_sq(s)

    def fn():
        stopline = ""
        if curline:
            line = get_cur_line()
            stopline = ", {}".format(line)

        cmd = "search('{str}', '{flags}'{stopline})".format(str=s,
                flags="".join(flags), stopline=stopline)
        line = int(vim.eval(cmd))
        match = line != 0

        if match:
            return get_cursor_position()
        else:
            return None

    if move:
        pos = fn()
    else:
        with preserve_cursor():
            pos = fn()

    return pos

def get_leader():
    return vim.eval("mapleader")

def keys(k, keymaps=True):
    """ feeds keys into vim as if you pressed them """

    k = escape_string_dq(k)
    cmd = "normal"
    if keymaps:
        # vim does not expand "\<leader>" in execute normal 
        # here we check explicitly for for the word leader in keys, before
        # attempting to run get_leader(), the reason is because on some versions
        # of vim, vim.eval will print an "invalid expression" error if a
        # variable is undefined, and that will look terrible for scripts that
        # press keys if a user's leader is undefined
        if "leader" in k.lower():
            k = _LEADER_REGEX.sub(get_leader() or "", k)
    else:
        cmd += "!"
    command('execute "%s %s"' % (cmd, k))

def get_register(name):
    val = vim.eval("@%s" % name)
    if val == "":
        val = None
    return val

def clear_register(name):
    set_register(name, "")

def set_register(name, val):
    val = escape_string_dq(str(val))
    command('let @%s = "%s"' % (name, val))

@preserve_state()
def get_word():
    """ gets the word under the cursor """
    keys('"0yiw')
    return get_register("0")

def delete_word():
    """ deletes the word under the cursor """
    # we don't do a @preserve_state because we want the cursor to move as the
    # word is deleted
    with preserve_mode():
        with preserve_registers("0"):
            keys('"0diw')
            return get_register("0")

@preserve_state()
def replace_word(rep):
    """ replaces the word under the cursor with rep """
    set_register("0", rep)
    keys('viw"0p')

@preserve_state()
def get_in_quotes():
    """ gets the string beneath the cursor that lies in either double or single
    quotes """
    keys("yi\"")
    val = get_register("0")
    if val is None:
        keys("yi'")
        val = get_register("0")
    return val


def key_map(key, maybe_fn=None, mode=NORMAL_MODE, recursive=False,
        local=False, **addl_options):
    """ a function to bind a key to some action, be it a vim action or a python
    function.  key_map takes a vim keymapping as the first argument """

    # we're using key_map as a decorator
    if maybe_fn is None:
        def wrapper(fn):
            key_map(key, fn, mode=mode, recursive=recursive, local=local,
                    **addl_options)
            return fn
        return wrapper

    map_command = "map"
    if not recursive:
        map_command = "nore" + map_command
    if mode:
        map_command = mode + map_command

    if local:
        map_command = map_command + " <buffer>"

    if callable(maybe_fn):
        fn = maybe_fn
        fn_takes_selection = len(inspect.getfullargspec(fn).args)

        # if we're mapping in visual mode, we're going to assume that the
        # function takes the contents of the visual selection.  if the function
        # returns something, let's replace the visual selection with it.  i
        # think these are reasonable assumptions
        if mode == VISUAL_MODE:
            old_fn = fn
            @wraps(fn)
            def wrapped():
                # only if we're expecting a selection should we pass the
                # selection.  this has side effects
                if fn_takes_selection:
                    sel = get_visual_selection()
                    rep = old_fn(sel)
                else:
                    rep = old_fn()

                if rep is not None:
                    replace_visual_selection(rep)
                if addl_options.get("preserve_selection", False):
                    reselect_last_visual_selection()
            fn = wrapped

        call = register_fn(fn)
        command("%s <silent> %s :%s %s<CR>" % (map_command, key, PYTHON_CMD, call))

    else:
        command("%s %s %s" % (map_command, key, maybe_fn))


visual_key_map = partial(key_map, mode=VISUAL_MODE)

def redraw():
    command("redraw!")

def step():
    """ simple debugging tool to see what the hell has happened in your script
    so far """
    redraw()
    time.sleep(1)

@preserve_state()
def get_visual_range():
    """ returns the start (row, col) and end (row, col) of our range in visual
    mode """
    keys("\<esc>gv")
    _, start_row, start_col, _ = vim.eval("getpos('v')")
    start_row = int(start_row)
    start_col = int(start_col)
    with preserve_cursor():
        keys("`>")
        end_row, end_col = get_cursor_position()
    return (start_row, start_col), (end_row, end_col)

def set_buffer(buf):
    command("buffer %d" % buf)

def get_current_buffer():
    return int(vim.eval("bufnr('%')"))

def get_num_buffers():
    i = int(vim.eval("bufnr('$')"))
    j = 0
    while i >= 1:
        listed = bool(int(vim.eval("buflisted(%d)" % i)))
        if listed:
            j += 1
        i -= 1
    return j


def get_current_window():
    return int(vim.eval("winnr()"))

def get_num_windows():
    return int(vim.eval("winnr('$')"))

def get_window_of_buffer(buf):
    return int(vim.eval("bufwinnr(%d)" % buf))

def get_buffer_in_window(win):
    return int(vim.eval("winbufnr(%d)" % win))

def new_window(size=None, vertical=False):
    if vertical:
        cmd = "vsplit"
    else:
        cmd = "split"

    if size is not None:
        cmd = str(size) + cmd

    command(cmd)
    return get_current_window()


def toggle_option(name):
    command("set %s!" % name)

def multi_set_option(*names):
    """ convenience function for setting a ton of options at once, for example,
    in your .vimrc.py file.  regular strings are treated as options with no
    values, while list/tuple elements are considered name/value pairs"""
    for name in names:
        val = None
        if isinstance(name, (list, tuple)):
            name, val = name
        set_option(name, val)

def set_runtime_path(parts):
    rtp = ",".join(parts)
    set_option("rtp", rtp)

def get_runtime_path():
    rtp = get_option("rtp")
    return rtp.split(",")

def get_option(name):
    value = vim.eval("&%s" % name)
    return value

def set_option(name, value=None, local=False):
    cmd = "set"
    if local:
        cmd = "setlocal"

    if value is not None:
        command("%s %s=%s" % (cmd, name, value))
    else:
        command("%s %s" % (cmd, name))

def set_option_default(name):
    command("set %s&" % name)

def unset_option(name):
    command("set no%s" % name)

def set_local_option(name, value=None):
    if value is not None:
        command("setlocal %s=%s" % (name, value))
    else:
        command("setlocal %s" % name)

def _parse_buffer_flags(flags):
    mapping = {
        "u": "unlisted",
        "%": "current",
        "#": "alternate",
        "a": "active",
        "h": "hidden",
        "=": "readonly",
        "+": "modified",
        "x": "errors",
    }
    parsed = {}
    for k,name in mapping.items():
        parsed[name] = k in flags
    return parsed

def get_buffers():
    """ gets all the buffers and the data associated with them """
    out = command("ls", capture=True)
    match = _BUFFER_LIST_REGEX.findall(out)
    buffers = {}
    if match:
        for (num, flags, name) in match:
            num = int(num)
            buffers[num] = {
                "name": name,
                "flags": _parse_buffer_flags(flags)
            }
    return buffers

def new_buffer(name, type=BUFFER_SCRATCH):
    """ creates a new buffer """
    # creating a new buffer will switch to it, so we need to preserve our
    # current buffer
    with preserve_buffer():
        command("new")
        name = escape_string_sq(name)
        name = escape_spaces(name)
        command("file %s" % name)

        if type is BUFFER_SCRATCH:
            set_local_option("buftype", "nofile")
            set_local_option("bufhidden", "hide")
            set_local_option("noswapfile")

        buf = get_current_buffer()
    return buf

@preserve_state()
def get_visual_selection():
    keys("\<esc>gvy")
    val = get_register("0")
    return val

def replace_visual_selection(rep):
    with preserve_registers("a"):
        set_register("a", rep)
        keys("gvd")
        if is_last_line() and False:
            keys('"ap')
        else:
            keys('"aP')

def set_buffer_contents(buf, s):
    set_buffer_lines(buf, s.split("\n"))

def set_buffer_lines(buf, l):
    b = _get_buffer(buf)
    b[:] = l

def get_current_buffer_contents():
    return get_buffer_contents(get_current_buffer())

def get_buffer_contents(buf):
    contents = "\n".join(get_buffer_lines(buf))
    return contents

def get_buffer_lines(buf):
    b = _get_buffer(buf)
    return list(b)

def raw_input(prompt=""):
    """ designed to shadow python's raw_input function, because it behaves the
    same way, except in vim """
    command("call inputsave()")
    stuff = vim.eval("input('%s')" % escape_string_sq(prompt))
    command("call inputrestore()")
    return stuff

def multi_command(*cmds):
    """  convenience function for setting multiple commands at once in your
    .vimpy.rc, like "syntax on", "nohlsearch", etc """
    for cmd in cmds:
        command(cmd)


class AutoCommandContext(object):
    """ an object of this class is passed to functions decorated with one of our
    autocommand decorators.  its purpose is to give the decorated function
    access to buffer-local versions of our helper functions """

    def abbrev(self, *args, **kwargs):
        fn = partial(abbrev, local=True)
        return fn(*args, **kwargs)

    def let(self, *args, **kwargs):
        fn = partial(let, scope=NS_BUFFER)
        return fn(*args, **kwargs)

    def set_option(self, *args, **kwargs):
        fn = partial(set_option, local=True)
        return fn(*args, **kwargs)

    def visual_key_map(self, *args, **kwargs):
        fn = partial(visual_key_map, local=True)
        return fn(*args, **kwargs)

    def key_map(self, *args, **kwargs):
        fn = partial(key_map, local=True)
        return fn(*args, **kwargs)


def on_autocmd(event, filetype):
    """ A decorator for functions to trigger on AutoCommand events.
    Your function will be passed an instance of
    AutoCommandContext, which contains on it *buffer-local* methods that would
    be useful to you. A filetype of "*" matches all files.
    For a list of eligible events, try :help autocmd-events in vim.  
    """ 
    def wrapped(fn):
        au_name = _generate_autocommand_name(fn)
        command("augroup %s" % au_name)
        command("autocmd!")
        ctx = AutoCommandContext()
        call = register_fn(partial(fn, ctx))
        command("autocmd %s %s :%s %s" % (event, filetype, PYTHON_CMD, call))
        command("augroup END")
        return fn

    return wrapped

when_buffer_is = partial(on_autocmd, "FileType")
when_buffer_is.__doc__ = """ A decorator for functions you wish to run when the buffer
    filetype=filetype. This is useful if you want to set some keybindings for a
    python buffer that you just opened """


def set_filetype(pat, ftype):
    s = "au BufRead,BufNewFile {pat} set filetype={ftype}"
    s = s.format(pat=pat, ftype=ftype)
    command(s)


if "snake.plugin_loader" in sys.modules:
    plugin_loader = reload(plugin_loader)
else:
    from . import plugin_loader
