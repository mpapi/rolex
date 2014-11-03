#!/usr/bin/env python

import locale
locale.setlocale(locale.LC_ALL, "")

from ConfigParser import RawConfigParser
from contextlib import contextmanager
from cStringIO import StringIO
from datetime import datetime
from difflib import SequenceMatcher
from Queue import Queue, Empty
from subprocess import check_output, CalledProcessError, Popen, PIPE
from threading import Event, Thread
import argparse
import curses
import curses.textpad
import os
import re
import time


class Command(object):
    """
    A shell command that `rolex` periodically runs. Configures the way the
    command is run, and also tracks some state, like the output of the last n
    runs of the command.
    """
    def __init__(self, command, period, running):
        self.command = command
        self.selected = False
        self.period = period
        self.running = running
        self.content = []
        self.mark = None
        self.started = time.ctime()
        self.last_update = time.ctime()
        self.runner = None
        self.terminated = False
        self.scheduled_run = Event()
        self.on_error = None
        self.on_change = None

        self.active = True
        self.latch = Event()

        self.friendly_time = False

    def start_runner(self, queue):
        """
        Starts a thread that periodically runs the command.

        `queue` is the queue into which the command's output is placed after
        each run.
        """
        self.runner = spawn(self._run, queue)

    def stop_runner(self):
        """
        Stops the thread that runs the command, as when the user kills the pane
        that's showing this command. If there isn't a thread currently running,
        this does nothing.
        """
        self.terminated = True

    def toggle_running(self):
        """
        Toggles the active state of the command, for pausing/unpausing this
        command independently of the others.
        """
        self.set_running(not self.active)

    def set_running(self, running):
        """
        Sets the active state of the command to `running`.
        """
        self.active = running
        if self.active:
            self.latch.set()
        else:
            self.latch.clear()

    def change_period(self, amount):
        """
        Changes the command's period by `amount`. (A positive amount makes the
        command run less frequently.)

        It is not possible to have a period shorter than one second.
        """
        self.period = max(1, self.period + amount)

    def set_diff_mark(self):
        """
        Sets the output of the command's last run as the "mark" for diffing
        against, i.e., the UI will show diffs against the point in time just
        before this function was called.
        """
        if self.content:
            self.mark = self.content[-1].splitlines()

    def clear_diff_mark(self):
        """
        Clears the diff mark set by `set_diff_mark`. If there isn't a diff
        mark, this does nothing.
        """
        self.mark = None

    def trigger_rerun(self, reset_schedule=False):
        """
        Signals for the command to be run again more or less immediately.

        If `reset_schedule` is True, the next command won't run again for
        approximately `self.period` seconds. Otherwise, next run will occur
        when it normally would have.
        """
        self.scheduled_run.set()
        self.latch.set()
        if reset_schedule:
            self.next_run = time.time()

    @property
    def diff_base_output(self):
        """
        The output, as a list of strings, to diff against.

        If a diff mark has been set, returns that. Otherwise, returns the
        output from two runs ago (to compare against the most recent output),
        or None if there isn't enough output for that.
        """
        if self.mark is not None:
            return self.mark
        if len(self.content) < 2:
            return None
        return self.content[-2].splitlines()

    def last_update_text(self, use_since=False):
        """
        Returns a string representation of the last time a command was run.
        """
        return self.last_update

    def wait_until_next_run(self, now):
        """
        Waits until the time of the next scheduled rerun (or returns
        immediately if that time is in the past).

        Returns True if the command should be run again, False if it doesn't
        need to.
        """
        result = self.scheduled_run.wait(self.next_run - now)
        return self.latch.is_set() and (result or self.next_run - now > 0)

    def _get_output(self):
        """
        Runs the command, returning its output and True.

        If the command failed to run, returns an error message and False
        instead.
        """
        try:
            with open(os.devnull, 'w') as devnull:
                output = check_output(self.command, stderr=devnull, shell=True)
                return output, True
        except CalledProcessError, e:
            return "Error running '%s':\n\n%s" % (self.command, e), False

    def _record_output(self, output):
        changed = self.content and (output != self.content[-1])
        # TODO diff against last, store time and data
        self.content.append(output)
        if len(self.content) > 60:
            self.content.pop(0)
        return changed

    def _compute_next_run(self, now):
        next_run = self.next_run
        while next_run < now:
            next_run += max(1, self.period)
        return next_run

    def _handle_result(self, handler, queue):
        if handler[0] == 'exit':
            queue.put(('exit', self))
        elif handler[0] == 'pause':
            queue.put(('pause', self))
        elif handler[0] == 'exec':
            Popen(handler[1], shell=True)
        elif handler[0] == 'exec_and_pause':
            queue.put(('pause', self))
            Popen(handler[1], shell=True)

    def _run(self, queue):
        """
        Periodically runs the command (forever), adding its output to `queue`.

        This is intended to run in a separate greenlet/thread/process.
        """
        self.trigger_rerun(reset_schedule=True)
        while True:
            if self.terminated:
                break
            now = time.time()
            if self.wait_until_next_run(now):
                output, success = self._get_output()
                if not success and self.on_error is not None:
                    self._handle_result(self.on_error, queue)
                self.last_update = time.ctime()
                changed = self._record_output(output)
                if changed and self.on_change is not None:
                    self._handle_result(self.on_change, queue)
                queue.put(('output', (self, output)))
                self.scheduled_run.clear()

            self.next_run = self._compute_next_run(now)

            self.running.wait()

            if not self.active:
                self.latch.clear()
            else:
                self.latch.set()
            self.latch.wait()


class Pane(object):
    """
    A fixed slice of the UI, to which information about a `Command` can be
    rendered.

    This manages a curses pad, rendering to that off-screen and then drawing
    the pad into the correct part of the screen.
    """
    def __init__(self, index, height, width, layout):
        """
        - `index` is 0 for the topmost pane on the screen, 1 for the one below
          that, etc.
        - `height` is the height of the pane, in terminal rows
        - `width` is the width of the pane, in terminal columns
        - `layout` is the layout manager for the pane
        """
        self.index = index
        self.show_diffs = False
        self.pattern = None
        self.graph = False
        self.browsing = False
        self.browsing_at = -1

        self.layout = layout
        self.resize(height, width)

    def resize(self, height, width):
        """
        Adjusts the height and width of the pane to be `height` terminal rows
        and `width` columns.
        """
        self.height = height
        self.width = width
        self.pad = curses.newpad(height, width)

    def draw_header(self, command, use_unicode=True, use_since=False):
        """
        Renders the header for the command to the internal curses pad.

        - `command` is the Command object to show in this pane.
        - `use_unicode` determines whether Unicode characters can be used to
          help draw the header
        - `use_since` determines whether the header timestamp is shown in
          absolute time or relative time
        """
        # Draw a full-width separator.
        separator = u'\u2500' if use_unicode else '-'
        self.pad.addstr(0, 0,
                        (separator * self.width).encode('utf-8'),
                        curses.color_pair(2))

        # Write the command's period.
        pos = 2  # margin
        self.pad.addstr(0, pos, str(command.period),
                        curses.color_pair(1) | curses.A_BOLD)
        pos += len(str(command.period)) + 1

        # Attributes for the command depend on whether it's running and/or
        # selected.
        attrs = curses.color_pair(3)
        if not command.running.is_set() or not command.active:
            attrs = curses.color_pair(4) | curses.A_BOLD
        if command.selected:
            attrs |= curses.A_UNDERLINE

        # Write the command.
        self.pad.addstr(0, pos, command.command, attrs)
        pos += len(command.command) + 1

        if self.browsing:
            self.pad.addstr(0, pos, 'browse', curses.color_pair(1))
            pos += len('browse') + 1

        if command.on_error is not None:
            msg = 'err:' + command.on_error[0]
            self.pad.addstr(0, pos, msg, curses.color_pair(1))
            pos += len(msg) + 1

        if command.on_change is not None:
            msg = 'chg:' + command.on_change[0]
            self.pad.addstr(0, pos, msg, curses.color_pair(1))
            pos += len(msg) + 1

        # Add markers for other states.
        if self.graph:
            self.pad.addstr(0, pos, 'graph', curses.color_pair(1))
        elif self.show_diffs:
            diff_str = 'diff last' if not command.mark else 'diff mark'
            self.pad.addstr(0, pos, diff_str, curses.color_pair(1))
        elif self.pattern:
            self.pad.addstr(0, pos, self.pattern, curses.color_pair(1))

        # Write the command's last run time, right-aligned.
        last_update = command.last_update_text(use_since=use_since)
        self.pad.addstr(0, self.width - len(last_update) - 1, last_update)

    def draw_wait(self):
        """
        Draws a "waiting" banner in the center of the pane.
        """
        output = [''] * (self.height / 2 - 2)
        output.append('Waiting for output...'.center(self.width))
        self.draw_output(output)

    def draw_graph(self, output):
        """
        Renders `output`, which is assumed to be a list of string lines
        containing floating point values, as an ASCII/Unicode time-series bar
        graph.
        """
        lines = (line for line in (line.strip() for line in output) if line)
        graph = Graph(self.height - 3, self.width - 1, lines, y_offset=2)
        graph.render(self.pad)

    def draw_output(self, output, diff_base=None, history=None):
        """
        Draws the output of a command, as a list of strings, to the pane.

        If `diff_base` (a list of strings) is given, the differences between
        `output` and `diff_base` are highlighted.
        """
        # Clear the pane, except the header.
        self.pad.move(1, 0)
        self.pad.clrtobot()

        if self.graph and self.pattern and history:
            # TODO None -> unknown (for gap in graph)
            pattern_matches = [
                re.findall('[-0-9.]+', match[0])[0] for match in
                (re.findall(self.pattern, line)
                 for output in history for line in output.splitlines())
                if match]
            return self.draw_graph(pattern_matches)
        elif self.graph:
            return self.draw_graph(output)

        for lineno, line in enumerate(output[-(self.height - 2):]):
            # Write the line, making sure it can fit in the pane.
            truncline = line.rstrip()[:self.width - 1]
            self.pad.addstr(2 + lineno, 1, truncline)

            # Highlight diffs if we're in diff mode and have something to diff
            # against.
            if self.show_diffs and diff_base and lineno < len(diff_base):
                diffline = diff_base[lineno][:self.width - 1]
                for pos, substr in get_diffs(diffline, truncline):
                    self.pad.addstr(2 + lineno, 1 + pos, substr,
                                    curses.color_pair(5) | curses.A_BOLD)

            # Highlight pattern matches if we have a pattern.
            elif self.pattern:
                for pos, substr in get_matches(self.pattern, truncline):
                    self.pad.addstr(2 + lineno, 1 + pos, substr,
                                    curses.color_pair(5) | curses.A_BOLD)

    def refresh(self, command):
        """
        Redraws the header and output area for `command`, but does not commit.
        """
        self.draw_header(command)
        if not command.content:
            self.draw_wait()
        else:
            self.draw_output(command.content[-1].splitlines(),
                             diff_base=command.diff_base_output,
                             history=command.content)

    def commit(self):
        """
        Writes the changes to the pad out to the window, but doesn't redraw.
        """
        self.layout.commit(self.pad, self.index, self.height, self.width)


class EvenVerticalLayout(object):
    """
    A layout that draws panes full-width, stacking them equally-sized
    vertically.
    """
    def size(self, size, index, height, width):
        return height / size, width

    def commit(self, pad, index, height, width):
        pad.noutrefresh(0, 0,
                        index * height, 0,
                        (index + 1) * height - 2, width)


class EvenHorizontalLayout(object):
    """
    A layout that draws panes full-height, stacking them equally-sized
    horizontally.
    """
    def size(self, size, index, height, width):
        return height, width / size

    def commit(self, pad, index, height, width):
        pad.noutrefresh(0, 0,
                        0, index * width,
                        height, (index + 1) * width - 2)


class Graph(object):
    """
    Renders a time-series bar graph from a set of points.
    """
    def __init__(self, graph_height, graph_width, values,
                 y_offset=1, x_offset=1, y_labels=4):
        self.graph_height = graph_height
        self.graph_width = graph_width
        self.y_offset = y_offset
        self.x_offset = x_offset
        self.y_labels = y_labels

        self.points = [float(value) for value in values]

        # Collapse (average) adjacent points if we have more points than
        # available width for each one to have a bar.
        if self.graph_width < len(self.points):
            points_per_col = len(self.points) / float(self.graph_width)
            points_per_col = max(2, int(round(points_per_col)))
            new_points = []
            for i in range(0, len(self.points), points_per_col):
                col_slice = self.points[i:i + points_per_col]
                new_points.append(sum(col_slice) / float(len(col_slice)))
            self.points = new_points

    @property
    def bar_width(self):
        """
        Computes the width of a bar in the graph, to come as close as possible
        to filling the allowed width.
        """
        return max(1, self.graph_width / len(self.points))

    def render(self, pad, fill=u'\u2592'):
        """
        Renders the bar to `pad`, using `fill` as the character to draw bars.
        """
        min_point, max_point = min(self.points), max(self.points)

        # Set up a matrix of characters (all spaces at first).
        graph = [[' ' for _ in range(self.graph_width)]
                 for _ in range(self.graph_height)]

        # Update the matrix with the fill char.
        delta = max(1, (max_point - min_point))
        for col, point in enumerate(self.points):
            for col_offset in range(self.bar_width):
                pct_height = (point - min_point) / delta
                bar_height = int(pct_height * self.graph_height)
                for row in range(bar_height):
                    y = self.graph_height - 1 - row
                    x = col * self.bar_width + col_offset
                    graph[y][x] = fill

        # Render the matrix to the curses pad.
        graph_lines = (u''.join(line).encode('utf8') for line in graph)
        for lineno, line in enumerate(graph_lines):
            pad.addstr(self.y_offset + lineno, self.x_offset, line)

        # Render y-axis labels.
        for label in range(self.y_labels + 1):
            pct = float(label) / self.y_labels
            y = 1 + int(self.graph_height * (1 - pct))
            pad.addstr(y, 1, str(min_point + pct * delta))


def get_matches(pattern, line):
    """
    Generates pairs of (column, matching substring) each match of the regular
    expression `pattern` in the string `line`.
    """
    for match in re.finditer(pattern, line):
        yield match.start(), line[match.start():match.end()]


def get_diffs(old_line, new_line):
    """
    Generates pairs of (column, different substring) for each diff between the
    strings `old_line` and `new_line`.
    """
    sm = SequenceMatcher(a=old_line, b=new_line)
    for tag, _, _, j1, j2 in sm.get_opcodes():
        if tag not in ('replace', 'insert'):
            continue
        yield j1, new_line[j1:j2]


class Screen(object):
    """
    Provides some high level functions around a curses screen.
    """

    @staticmethod
    @contextmanager
    def configure(colors):
        """
        A context manager that sets up a screen, yields it, and cleans the
        screen up when finished (ensuring that the terminal is usable again).

        Like `curses.wrapper` but a bit more tailored.

        - `colors` is a list of (foreground color, background color) pairs
        """
        screen = curses.initscr()
        curses.start_color()
        for i, (fg, bg) in enumerate(colors, start=1):
            curses.init_pair(i, fg, bg)

        curses.noecho()
        curses.cbreak()
        curses.curs_set(0)
        screen.keypad(1)
        try:
            yield Screen(screen)
        finally:
            screen.keypad(0)
            curses.nocbreak()
            curses.echo()
            curses.endwin()

    def __init__(self, screen):
        """
        - `screen` is a curses screen, as from `curses.initscr`
        """
        self._screen = screen
        self.update_size()

    def get_keys(self, queue):
        """
        Reads keystrokes from the screen (forever), and adds them to `queue`.

        This is intended to run in a separate greenlet/thread/process.
        """
        self._screen.nodelay(1)
        esc = False
        while True:
            ch = self._screen.getch()
            if ch == -1:
                time.sleep(0.1)
            elif ch == 27:
                esc = True
            else:
                queue.put(('key', ch if not esc else 'M-' + chr(ch)))
                esc = False

    def update_size(self):
        """
        Updates the size of the terminal in which `rolex` is running.
        """
        self.height, self.width = self._screen.getmaxyx()

    def message_user(self, message_string, delay=3):
        """
        Writes a message to the user at the bottom of the screen, erasing it
        after `delay` seconds.

        (Currently, there is no protection from overwriting other messages or
        prompts.)
        """
        self._screen.addstr(self.height - 1, 0, message_string)
        self._screen.refresh()
        spawn(self._destroy_message, delay)

    def _destroy_message(self, delay):
        """
        Erases the message set by `message_user` after `delay` seconds.
        """
        time.sleep(delay)
        self._screen.move(self.height - 1, 0)
        self._screen.clrtobot()

    def prompt_user(self, prompt_string, default_value):
        """
        Prompt the user with a bar at the bottom of the screen.

        `prompt_string` is the uneditable string displayed at the left-hand
        side of the bar, and `default_value` (if given) is the editable string
        that populates the bar.
        """
        self._screen.addstr(self.height - 1, 0, prompt_string)
        self._screen.refresh()

        # Create a window at the bottom of the screen, set the default value,
        # wrap it in a Textbox.
        textwin = curses.newwin(1, self.width, self.height - 1,
                                len(prompt_string))
        textwin.addstr(0, 0, default_value or '')
        textbox = curses.textpad.Textbox(textwin)

        curses.curs_set(1)
        try:
            result = textbox.edit().strip()
        finally:
            curses.curs_set(0)

        # Erase the bar.
        self._screen.move(self.height - 1, 0)
        self._screen.clrtobot()

        return result

    def clear_and_refresh(self):
        """
        Clear the screen immediately.
        """
        self._screen.clear()
        self._screen.refresh()


class Watch(object):
    """
    Ties together a screen, list of commands, and a list of panes.
    """
    def __init__(self, screen, running, commands, queue):
        """
        - `screen` is a `Screen` object
        - `running` is an `Event` that can be used to pause commands
        - `commands` is an initial list of `Command` objects to run
        - `queue` is a queue that the screen and commands will use to send key
          presses and command output
        """
        self.screen = screen
        self.commands = commands
        self.queue = queue
        self.running = running
        self.layout = LAYOUTS[0]

        self.panes = [Pane(i, 1, 1, self.layout) for i in range(len(commands))]

        # This maps pane index to the command to show in that pane, so we can
        # rearrange them.
        self.pane_map = dict(enumerate(self.commands))

        self.adjust_pane_sizes()

    @contextmanager
    def suspended(self):
        """
        A context manager that pauses all commands for the duration of the
        execution of the `with` block and allows other applications to take
        over the screen.
        """
        curses.def_prog_mode()
        self.screen.clear_and_refresh()
        self.running.clear()
        try:
            yield
        finally:
            self.running.set()
            curses.reset_prog_mode()
            self.screen.clear_and_refresh()

    def set_selected_from_key(self, key):
        """
        Sets the selected pane from the keycode `key`.

        So, if `key` is the keycode for "2", the command currently in the
        second pane from the top is selected (and all others are deselected).
        """
        for i, c in self.pane_map.iteritems():
            c.selected = i == int(chr(key)) - 1

    def add_pane_and_command(self, command, period):
        """
        Adds `command` to run every `period` seconds in a new pane.
        """
        pane = Pane(len(self.commands), 1, 1, self.layout)
        self.panes.append(pane)

        command = Command(command, period, self.running)
        command.start_runner(self.queue)
        self.commands.append(command)
        self.pane_map = dict(enumerate(self.commands))

    def remove_pane(self, pane):
        """
        Removes the Pane object `pane`, so that it no longer renders.
        """
        self.panes.remove(pane)
        for i, p in enumerate(self.panes):
            p.index = i

    def remove_command(self, command):
        """
        Removes the Command object `command`, so that it no longer runs.
        """
        command.stop_runner()
        self.commands.remove(command)
        if not any(c.selected for c in self.commands) and self.commands:
            self.commands[0].selected = True
        self.pane_map = dict(enumerate(self.commands))
        return len(self.commands) == 0

    def set_layout(self, new_layout):
        """
        Sets `new_layout` as the new layout to use when drawing the screen,
        and immediately resizes/redraws all panes accordingly.
        """
        self.layout = new_layout
        for pane in self.panes:
            pane.layout = new_layout
        self.adjust_pane_sizes()

    def adjust_pane_sizes(self):
        """
        Resize all panes so that they're all evenly about (screen height / # of
        commands) rows high.
        """
        self.screen.update_size()
        self.screen.clear_and_refresh()

        if not self.commands:
            return

        for pane, command in self:
            new_height, new_width = self.layout.size(len(self.commands),
                                                     pane.index,
                                                     self.screen.height,
                                                     self.screen.width)
            pane.resize(new_height, new_width)
            pane.refresh(command)
            pane.commit()

    def __iter__(self):
        """
        Generate a list of pairs of (Pane object, Command object for the
        command that runs in that pane).
        """
        for i, command in sorted(self.pane_map.iteritems()):
            yield self.panes[i], command

    def pane_for_command(self, command):
        """
        Looks up the Pane object to which the Command `command` currently
        renders.
        """
        for i, c in self.pane_map.iteritems():
            if c == command:
                return self.panes[i]
        return None

    @property
    def selected(self):
        """
        The pair (Pane object for currently selected pane, Command object for
        command that runs in that pane), or None.
        """
        for pane, command in self:
            if command.selected:
                return pane, command


def cmd_select(watch, key):
    """
    Sets the selected command given the keycode `key` (where "1" selects the
    command in the topmost pane, "2" selects the one below that, etc.).
    """
    watch.set_selected_from_key(key)
    for pane, command in watch:
        pane.draw_header(command)
        pane.commit()


def cmd_toggle_pause(watch, key):
    """
    Toggles the "paused" flag for all commands.
    """
    all_active = all(command.active for _, command in watch)
    for pane, command in watch:
        command.set_running(not all_active)
        pane.draw_header(command)
        pane.commit()


def cmd_toggle_pause_one(watch, key):
    """
    Toggles the "paused" flag for the selected commands.
    """
    pane, command = watch.selected
    command.toggle_running()
    pane.draw_header(command)
    pane.commit()


def cmd_period_change(amount):
    """
    Updates the period of execution for the selection command,
    adjusting it by `amount`.
    """
    def _cmd(watch, key):
        pane, command = watch.selected
        command.change_period(amount)
        pane.draw_header(command)
        pane.commit()
    return _cmd


def cmd_toggle_diffs(watch, key):
    """
    Toggles diff mode for the command in the selected pane. If diffs of any
    kind are enabled (diff last or diff mark), they are disabled; otherwise,
    diff last is enabled.
    """
    pane, command = watch.selected
    command.clear_diff_mark()
    pane.show_diffs = not pane.show_diffs
    pane.draw_header(command)
    pane.commit()


def cmd_edit_pattern(watch, key):
    """
    Opens a prompt to set or edit a regular expression for highlighting the
    output of the selected pane's command.
    """
    pane, command = watch.selected
    new_pattern = watch.screen.prompt_user('Pattern: ', pane.pattern or '')
    pane.pattern = new_pattern or None
    pane.refresh(command)
    pane.commit()


def cmd_edit_command(watch, key):
    """
    Opens a prompt to set or edit the command that runs in the selected pane.
    """
    pane, command = watch.selected
    new_command = watch.screen.prompt_user('Command: ', command.command)
    if not new_command:
        return
    command.command = new_command
    command.trigger_rerun(reset_schedule=True)


def cmd_add_command(watch, key):
    """
    Opens a series of prompts to start periodically running a new command in a
    new pane.
    """
    command = watch.screen.prompt_user('Run: ', '')
    if not command:
        return
    try:
        period = int(watch.screen.prompt_user('Period: ', ''))
    except ValueError:
        return
    watch.add_pane_and_command(command, period)
    watch.adjust_pane_sizes()


def cmd_kill_command(watch, key):
    """
    Kills the selected pane and stops running the command it contains.
    """
    pane, command = watch.selected
    watch.remove_pane(pane)
    if watch.remove_command(command):
        return True
    watch.adjust_pane_sizes()


def cmd_mark_diff(watch, key):
    """
    Enables diff mark mode in the selected pane. This diffs all future output
    of the command in that pane against the output that is currently show
    there.
    """
    pane, command = watch.selected
    pane.show_diffs = True
    command.set_diff_mark()
    pane.draw_header(command)
    pane.commit()


def cmd_force_run(with_reset):
    """
    Forces the command in the selected pane to run immediately. This does not
    alter the time at which the next scheduled run of the command was supposed
    to occur.
    """
    def _cmd(watch, key):
        _, command = watch.selected
        command.trigger_rerun(reset_schedule=with_reset)
    return _cmd


def cmd_show_help(watch, key):
    """
    Shows the current keybindings. Uses $PAGER, or `less.
    """
    with watch.suspended():
        proc = Popen(os.environ.get('PAGER', 'less'), stdin=PIPE, shell=True)
        proc.communicate(generate_help_text())

    for pane, command in watch:
        pane.draw_header(command)
        pane.commit()


def cmd_toggle_graph(watch, key):
    """
    Toggles graph mode for the command in the selected pane.
    """
    pane, command = watch.selected
    pane.graph = not pane.graph
    command.trigger_rerun()
    pane.draw_header(command)
    pane.commit()


def cmd_cycle_layout(watch, key):
    """
    Toggles graph mode for the command in the selected pane.
    """
    new_layout = LAYOUTS[(LAYOUTS.index(watch.layout) + 1) % len(LAYOUTS)]
    watch.set_layout(new_layout)


def cmd_rotate_panes(amount):
    """
    Rotates panes, popping the first `amount` and tacking them on the end of
    the ordered arrangement of panes.
    """
    def _rotate(watch, key):
        commands = [cmd for _, cmd in sorted(watch.pane_map.items())]
        watch.pane_map = dict(enumerate(commands[amount:] + commands[:amount]))
        watch.adjust_pane_sizes()
    return _rotate


def cmd_back_output(watch, key):
    """
    Puts the selected pane in "browsing mode" if it's not already, and shows
    the output of the run before the one that's currently displayed.
    """
    pane, command = watch.selected
    pane.browsing = True
    if pane.browsing_at > -len(command.content):
        pane.browsing_at -= 1
    pane.draw_header(command)
    pane.draw_output(command.content[pane.browsing_at].splitlines(),
                     diff_base=command.diff_base_output)
    pane.commit()


def cmd_forward_output(watch, key):
    """
    Puts the selected pane in "browsing mode" if it's not already, and shows
    the output of the run after the one that's currently displayed.
    """
    pane, command = watch.selected
    pane.browsing = True
    if pane.browsing_at < -1:
        pane.browsing_at += 1
    # TODO show the time of the command run we're displaying too
    pane.draw_header(command)
    pane.draw_output(command.content[pane.browsing_at].splitlines(),
                     diff_base=command.diff_base_output)
    pane.commit()


def cmd_current_output(watch, key):
    """
    Takes the selected pane out of "browsing mode" and shows the output of the
    most recent command run.
    """
    pane, command = watch.selected
    pane.browsing = False
    pane.browsing_at = -1
    command.trigger_rerun()
    pane.draw_header(command)
    pane.commit()


def cmd_write_config(watch, key):
    """
    Writes a config file (for now, in the working directory) describing the
    current layout.
    """
    config = RawConfigParser()
    for pane, command in watch:
        section = 'pane %d' % pane.index
        config.add_section(section)
        for field in ['show_diffs', 'graph', 'pattern', 'height', 'width']:
            value = getattr(pane, field)
            if value is not None:
                config.set(section, field, value)
        config.set(section, 'layout', pane.layout.__class__.__name__)
        for field in ['command', 'period', 'selected', 'active']:
            config.set(section, field, getattr(command, field))

    conf_path = 'rolex.%s.conf' % datetime.now().isoformat()
    with open(conf_path, 'w') as conf:
        config.write(conf)

    watch.screen.message_user('Wrote conf to %s' % conf_path)


def cmd_exit_on_error(watch, key):
    """
    Toggles exit-on-error for the selected pane.
    """
    pane, command = watch.selected
    if command.on_error is not None and command.on_error[0] == 'exit':
        command.on_error = None
    else:
        command.on_error = ('exit',)
    pane.draw_header(command)
    pane.commit()


def cmd_pause_on_error(watch, key):
    """
    Toggles pause-on-error for the selected pane.
    """
    pane, command = watch.selected
    if command.on_error is not None and command.on_error[0] == 'pause':
        command.on_error = None
    else:
        command.on_error = ('pause',)
    pane.draw_header(command)
    pane.commit()


def cmd_exec_on_error(watch, key):
    """
    Sets up exec-on-error for the selected pane.
    """
    pane, command = watch.selected
    run = watch.screen.prompt_user('Run: ', '')
    if not run:
        command.on_error = None
        return
    should_pause = watch.screen.prompt_user('Pause? [y/n] ', 'y')
    should_pause = should_pause.lower() == 'y'
    command.on_error = ('exec_and_pause' if should_pause else 'exec', run)
    pane.draw_header(command)
    pane.commit()


def cmd_exit_on_change(watch, key):
    """
    Toggles exit-on-change for the selected pane.
    """
    pane, command = watch.selected
    if command.on_change is not None and command.on_change[0] == 'exit':
        command.on_change = None
    else:
        command.on_change = ('exit',)
    pane.draw_header(command)
    pane.commit()


def cmd_pause_on_change(watch, key):
    """
    Toggles pause-on-change for the selected pane
    """
    pane, command = watch.selected
    if command.on_change is not None and command.on_change[0] == 'pause':
        command.on_change = None
    else:
        command.on_change = ('pause',)
    pane.draw_header(command)
    pane.commit()


def cmd_exec_on_change(watch, key):
    """
    Sets up exec-on-change for the selected pane.
    """
    pane, command = watch.selected
    run = watch.screen.prompt_user('Run: ', '')
    if not run:
        command.on_change = None
        return
    should_pause = watch.screen.prompt_user('Pause? [y/n] ', 'y')
    should_pause = should_pause.lower() == 'y'
    command.on_change = ('exec_and_pause' if should_pause else 'exec', run)
    pane.draw_header(command)
    pane.commit()


LAYOUTS = [
    EvenVerticalLayout(),
    EvenHorizontalLayout()
]


# Curses color config.
COLORS = [
    (3, 0),
    (7, 0),
    (6, 0),
    (0, 0),
    (1, 0)
]


# A mapping of keycodes to functions of (`Watch` object, keycode).
KEYBINDINGS = {
    curses.KEY_RESIZE: (lambda watch, d: watch.adjust_pane_sizes(), None),
    ord('r'): (lambda watch, d: watch.adjust_pane_sizes(), 'redraw panes'),
    ord('q'): (lambda w, d: True, 'quit'),
    ord('1'): (cmd_select, 'select pane 1'),
    ord('2'): (cmd_select, 'select pane 2'),
    ord('3'): (cmd_select, 'select pane 3'),
    ord('4'): (cmd_select, 'select pane 4'),
    ord('5'): (cmd_select, 'select pane 5'),
    ord('6'): (cmd_select, 'select pane 6'),
    ord('7'): (cmd_select, 'select pane 7'),
    ord('8'): (cmd_select, 'select pane 8'),
    ord('9'): (cmd_select, 'select pane 9'),
    ord(' '): (cmd_toggle_pause, 'pause/unpause all commands'),
    ord('P'): (cmd_toggle_pause_one, 'pause/unpause the current command'),
    ord('+'): (cmd_period_change(1), "increase selected pane's period"),
    ord('-'): (cmd_period_change(-1), "decrease selected pane's period"),
    ord('d'): (cmd_toggle_diffs, 'enable diff last mode, or disable diffs'),
    ord('m'): (cmd_mark_diff, 'enable diff mark mode'),
    ord('p'): (cmd_edit_pattern, 'set/edit highlight pattern'),
    ord('c'): (cmd_edit_command, 'edit the selected command'),
    ord('a'): (cmd_add_command, 'add a pane to run a new command'),
    ord('k'): (cmd_kill_command, 'kill the selected pane'),
    ord('f'): (cmd_force_run(False), 'run the selected command now'),
    ord('F'): (cmd_force_run(True), 'run the command and reset next run time'),
    ord('h'): (cmd_show_help, 'show help'),
    ord('?'): (cmd_show_help, 'show help'),
    ord('g'): (cmd_toggle_graph, 'toggle graph mode'),
    ord('o'): (cmd_cycle_layout, 'cycle layouts (vertical <-> horizontal)'),
    ord('['): (cmd_rotate_panes(1), 'rotate panes left/up'),
    ord(']'): (cmd_rotate_panes(-1), 'rotate panes right/down'),
    ord('<'): (cmd_back_output, 'go to output of previous command run'),
    ord('>'): (cmd_forward_output, 'go to next of previous command run'),
    ord('n'): (cmd_current_output, 'stop browsing command output'),
    ord('w'): (cmd_write_config, 'write current layout to a config file'),
    'M-e': (cmd_exit_on_error, 'set exit-on-error for the active pane'),
    'M-p': (cmd_pause_on_error, 'set pause-on-error for the active pane'),
    'M-x': (cmd_exec_on_error, 'set execon-error for the active pane'),
    'M-E': (cmd_exit_on_change, 'set exit-on-change for the active pane'),
    'M-P': (cmd_pause_on_change, 'set pause-on-change for the active pane'),
    'M-X': (cmd_exec_on_change, 'set exec-on-change for the active pane'),
}


def generate_help_text():
    """
    Generate a string of help text.
    """
    help_text = StringIO()
    print >> help_text, 'Keybindings:'
    print >> help_text
    for key, (func, desc) in sorted(KEYBINDINGS.iteritems()):
        if desc:
            print >> help_text, "  '%s'  %s" % (chr(key), desc)
    return help_text.getvalue()


def spawn(func, *args, **kwargs):
    """
    Runs `func(*args, **args)` in a separate greenlet/thread/process.

    (This implementation uses threads.)
    """
    thread = Thread(target=func, args=args, kwargs=kwargs)
    thread.daemon = True
    thread.start()
    return thread


def redraw(seconds=1):
    """
    Redraws the screen periodically.
    """
    while True:
        curses.doupdate()
        time.sleep(seconds)


def _read_config(conf_file, running):
    parser = RawConfigParser()
    parser.read([conf_file])
    for section in sorted(parser.sections()):
        if parser.has_option(section, 'pattern'):
            pattern = parser.get(section, 'pattern')
        else:
            pattern = None
        yield dict(command=parser.get(section, 'command'),
                   period=parser.getint(section, 'period'),
                   selected=parser.getboolean(section, 'selected'),
                   active=parser.getboolean(section, 'active'),
                   show_diffs=parser.getboolean(section, 'show_diffs'),
                   pattern=pattern,
                   layout=parser.get(section, 'layout'),
                   graph=parser.getboolean(section, 'graph'))


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--conf', type=str,
                        help='config file to restore from')
    parser.add_argument('-n', '--interval', type=int, default=2,
                        help='seconds to wait between updates')
    parser.add_argument('command', type=str, nargs='*',
                        help='initial command to run')
    return parser.parse_args()


def main():
    queue = Queue()

    running = Event()
    running.set()

    pane_overrides = None

    args = _parse_args()
    if args.conf:
        pane_overrides = []
        commands = []
        for pane in _read_config(args.conf, running):
            pane_overrides.append(pane)
            commands.append(Command(pane['command'], pane['period'], running))
    else:
        if not args.command:
            print 'Nothing to run.'
            return
        commands = [Command(' '.join(args.command), args.interval, running)]
        commands[0].selected = True

    with Screen.configure(COLORS) as screen:
        screen.clear_and_refresh()
        spawn(screen.get_keys, queue)
        spawn(redraw)

        watch = Watch(screen, running, commands, queue)
        for i, (pane, command) in enumerate(watch):
            if pane_overrides is not None:
                assert 0 <= i < len(pane_overrides)
                overrides = pane_overrides[i]
                command.selected = overrides['selected']
                command.active = overrides['active']
                pane.show_diffs = overrides['show_diffs']
                pane.pattern = overrides['pattern']
                pane.graph = overrides['graph']
                pane.layout = [
                    layout for layout in LAYOUTS
                    if layout.__class__.__name__ == overrides['layout']][0]

            command.start_runner(queue)
            pane.draw_header(command)
            pane.draw_wait()
            pane.commit()
        curses.doupdate()

        while True:
            try:
                tag, data = queue.get(timeout=0.1)
            except Empty:
                continue

            if tag == 'key':
                func, _ = KEYBINDINGS.get(data, (lambda w, k: None, None))
                if func(watch, data):
                    break
                curses.doupdate()
                continue
            elif tag == 'pause':
                for pane, command in watch:
                    command.set_running(False)
                    pane.draw_header(command)
                    pane.commit()
                continue
            elif tag == 'exit':
                break

            command, output = data
            pane = watch.pane_for_command(command)
            if not pane:
                continue

            if pane.browsing:
                continue

            pane.draw_header(command)
            pane.draw_output(output.splitlines(),
                             diff_base=command.diff_base_output,
                             history=command.content)
            pane.commit()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
