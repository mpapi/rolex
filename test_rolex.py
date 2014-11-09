import atexit
import os
import shutil
import tempfile
import textwrap

from mock import Mock, patch, call, ANY
from nose.tools import eq_

from rolex import Command, Pane, Watch, get_matches, get_diffs, _read_config
from rolex import EvenVerticalLayout


def _tempdir():
    temp = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, temp)
    return temp


def test_command_change_period():
    tests = [
        (1, 5, 6),
        (1, 0, 1),
        (1, -10, 1),
        (5, -1, 4)
    ]
    for start, adjustment, expected in tests:
        yield _verify_command_change_period, start, adjustment, expected


def _verify_command_change_period(start, adjustment, expected):
    cmd = Command('test', start, Mock())
    eq_(start, cmd.period)
    cmd.change_period(adjustment)
    eq_(expected, cmd.period)


def test_command_diff_mark():
    cmd = Command('test', 1, Mock())
    eq_(None, cmd.diff_base_output)

    cmd.set_diff_mark()
    eq_(None, cmd.diff_base_output)

    cmd.content.append('test\noutput')
    cmd.set_diff_mark()
    eq_(['test', 'output'], cmd.diff_base_output)

    cmd.clear_diff_mark()
    eq_(None, cmd.diff_base_output)


def test_command_diff_last():
    cmd = Command('test', 1, Mock())
    eq_(None, cmd.diff_base_output)

    cmd.content.append('test\noutput 1')
    eq_(None, cmd.diff_base_output)

    cmd.content.append('test\noutput 2')
    eq_(['test', 'output 1'], cmd.diff_base_output)


def test_command_toggle_running():
    cmd = Command('test1', 1, Mock())
    cmd.toggle_running()
    eq_(False, cmd.active)

    cmd.toggle_running()
    eq_(True, cmd.active)


def test_command_set_running():
    cmd = Command('test1', 1, Mock())
    cmd.set_running(False)
    eq_(False, cmd.active)

    cmd.set_running(True)
    eq_(True, cmd.active)


def test_command_get_output():
    cmd = Command('echo "test"', 1, Mock())
    eq_(("test\n", True), cmd._get_output())


def test_command_get_output_error():
    cmd = Command('false', 1, Mock())
    assert cmd._get_output()[0].startswith("Error running 'false'")


@patch('rolex.time')
@patch('rolex.curses')
def test_pad_draw_header(curses_mock, time_mock):
    time_mock.ctime.return_value = ctime = 'Tue Mar 25 21:00:00 2014'
    layout = EvenVerticalLayout()
    Pane(0, 25, 80, layout).draw_header(Command('test', 1, Mock()))
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', curses_mock.color_pair(3)),
        call(0, 55, ctime)
    ])


@patch('rolex.time')
@patch('rolex.curses')
def test_pad_draw_header_inactive(curses_mock, time_mock):
    time_mock.ctime.return_value = ctime = 'Tue Mar 25 21:00:00 2014'
    layout = EvenVerticalLayout()
    command = Command('test', 1, Mock())
    command.active = False
    Pane(0, 25, 80, layout).draw_header(command)
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', curses_mock.color_pair(4) | curses_mock.A_BOLD),
        call(0, 55, ctime)
    ])


@patch('rolex.time')
@patch('rolex.curses')
def test_pad_draw_header_on_error(curses_mock, time_mock):
    time_mock.ctime.return_value = ctime = 'Tue Mar 25 21:00:00 2014'
    layout = EvenVerticalLayout()
    command = Command('test', 1, Mock())
    command.on_error = ('exit',)
    Pane(0, 25, 80, layout).draw_header(command)
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', curses_mock.color_pair(3)),
        call(0, 9, 'err:exit', curses_mock.color_pair(1)),
        call(0, 55, ctime)
    ])


@patch('rolex.time')
@patch('rolex.curses')
def test_pad_draw_header_on_change(curses_mock, time_mock):
    time_mock.ctime.return_value = ctime = 'Tue Mar 25 21:00:00 2014'
    layout = EvenVerticalLayout()
    command = Command('test', 1, Mock())
    command.on_change = ('exit',)
    Pane(0, 25, 80, layout).draw_header(command)
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', curses_mock.color_pair(3)),
        call(0, 9, 'chg:exit', curses_mock.color_pair(1)),
        call(0, 55, ctime)
    ])


@patch('rolex.time')
@patch('rolex.curses')
def test_pad_draw_header_with_pattern(curses_mock, time_mock):
    time_mock.ctime.return_value = ctime = 'Tue Mar 25 21:00:00 2014'
    layout = EvenVerticalLayout()
    command = Command('test', 1, Mock())
    pane = Pane(0, 25, 80, layout)
    pane.pattern = r'\d+'
    pane.draw_header(command)
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', curses_mock.color_pair(3)),
        call(0, 9, r'\d+', curses_mock.color_pair(1)),
        call(0, 55, ctime)
    ])


@patch('rolex.time')
@patch('rolex.curses')
def test_pad_draw_header_with_diff(curses_mock, time_mock):
    time_mock.ctime.return_value = ctime = 'Tue Mar 25 21:00:00 2014'
    layout = EvenVerticalLayout()
    command = Command('test', 1, Mock())
    pane = Pane(0, 25, 80, layout)
    pane.show_diffs = True
    pane.draw_header(command)
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', curses_mock.color_pair(3)),
        call(0, 9, 'diff last', curses_mock.color_pair(1)),
        call(0, 55, ctime)
    ])
    curses_mock.reset()

    command.mark = 'test'
    pane.draw_header(command)
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', curses_mock.color_pair(3)),
        call(0, 9, 'diff mark', curses_mock.color_pair(1)),
        call(0, 55, ctime)
    ])


@patch('rolex.curses')
def test_pad_draw_wait(curses_mock):
    layout = EvenVerticalLayout()
    Pane(0, 25, 80, layout).draw_wait()
    curses_mock.newpad().addstr.assert_has_calls([
        call(i, 1, ANY) for i in range(2, 13)
    ])


def test_get_matches():
    eq_([(4, '12')], list(get_matches(r'\d+', 'test12test')))


def test_get_diffs():
    eq_([(4, '12')], list(get_diffs('test34test', 'test12test')))


@patch('rolex.curses')
def test_watch_set_selected(curses_mock):
    cmd1 = Command('test1', 1, Mock())
    cmd2 = Command('test2', 2, Mock())
    watch = Watch(Mock(height=20, width=80), Mock(), [cmd1, cmd2], Mock())

    eq_(True, watch.panes[0].selected)
    eq_(False, watch.panes[1].selected)

    watch.set_selected_from_key(ord('1'))

    eq_(True, watch.panes[0].selected)
    eq_(False, watch.panes[1].selected)

    watch.set_selected_from_key(ord('2'))

    eq_(False, watch.panes[0].selected)
    eq_(True, watch.panes[1].selected)


def test_read_config():
    temp = _tempdir()
    conf = os.path.join(temp, 'test.conf')
    with open(conf, 'w') as fil:
        print >> fil, textwrap.dedent("""
            [pane 0]
            show_diffs = False
            graph = False
            height = 100
            width = 100
            layout = EvenVerticalLayout
            command = test
            period = 2
            selected = True
            active = True
        """).strip()
    parsed = list(_read_config(conf, None))
    eq_(1, len(parsed))
    eq_(dict(command='test',
             period=2,
             selected=True,
             active=True,
             show_diffs=False,
             pattern=None,
             layout='EvenVerticalLayout',
             graph=False), parsed[0])
