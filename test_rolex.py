from mock import Mock, patch, call, ANY
from nose.tools import eq_

from rolex import Command, Pane, Watch, get_matches, get_diffs
from rolex import Event, EvenVerticalLayout


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


@patch('rolex.time')
@patch('rolex.curses')
def test_pad_draw_header(curses_mock, time_mock):
    time_mock.ctime.return_value = ctime = 'Tue Mar 25 21:00:00 2014'
    layout = EvenVerticalLayout()
    Pane(0, 25, 80, layout).draw_header(Command('test', 1, Mock()))
    curses_mock.newpad().addstr.assert_has_calls([
        call(0, 0, ANY, ANY),
        call(0, 2, '1', ANY),
        call(0, 4, 'test', ANY),
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
def test_watch_toggle_running(curses_mock):
    running = Event()

    cmd1 = Command('test1', 1, Mock())
    cmd2 = Command('test2', 2, Mock())
    watch = Watch(Mock(height=20, width=80), running, [cmd1, cmd2], Mock())
    eq_(False, running.is_set())

    watch.toggle_running()
    eq_(True, running.is_set())

    watch.toggle_running()
    eq_(False, running.is_set())


@patch('rolex.curses')
def test_watch_set_selected(curses_mock):
    cmd1 = Command('test1', 1, Mock())
    cmd2 = Command('test2', 2, Mock())
    watch = Watch(Mock(height=20, width=80), Mock(), [cmd1, cmd2], Mock())

    eq_(False, cmd1.selected)
    eq_(False, cmd2.selected)

    watch.set_selected_from_key(ord('1'))

    eq_(True, cmd1.selected)
    eq_(False, cmd2.selected)

    watch.set_selected_from_key(ord('2'))

    eq_(False, cmd1.selected)
    eq_(True, cmd2.selected)
