import atexit
import os
import shutil
import tempfile
import textwrap

from mock import Mock, patch, call, ANY
from nose.tools import eq_

from rolex import Command, Pane, Watch, get_matches, get_diffs, _read_config
from rolex import EvenVerticalLayout, EvenHorizontalLayout


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


def test_layout_size():
    cases = [
        (EvenHorizontalLayout(), (200, 40), (5, 0, 200, 200)),
        (EvenHorizontalLayout(), (200, 40), (5, 1, 200, 200)),
        (EvenHorizontalLayout(), (200, 40), (5, 2, 200, 200)),
        (EvenHorizontalLayout(), (200, 40), (5, 3, 200, 200)),
        (EvenHorizontalLayout(), (200, 40), (5, 4, 200, 200)),
        (EvenHorizontalLayout(), (100, 40), (5, 0, 100, 200)),
        (EvenHorizontalLayout(), (200, 39), (5, 0, 200, 199)),
        (EvenHorizontalLayout(), (199, 40), (5, 0, 199, 200)),
        (EvenVerticalLayout(), (40, 200), (5, 0, 200, 200)),
        (EvenVerticalLayout(), (40, 200), (5, 1, 200, 200)),
        (EvenVerticalLayout(), (40, 200), (5, 2, 200, 200)),
        (EvenVerticalLayout(), (40, 200), (5, 3, 200, 200)),
        (EvenVerticalLayout(), (40, 200), (5, 4, 200, 200)),
        (EvenVerticalLayout(), (20, 200), (5, 0, 100, 200)),
        (EvenVerticalLayout(), (40, 199), (5, 0, 200, 199)),
        (EvenVerticalLayout(), (39, 200), (5, 0, 199, 200)),
    ]
    for layout, expected, params in cases:
        yield _verify_layout_size, layout, expected, params


def _verify_layout_size(layout, expected, params):
    eq_(expected, layout.size(*params))


def test_layout_commit():
    cases = [
        (EvenHorizontalLayout(),
         (0, 0, 0, 0, 200, 198),
         (0, 200, 200)),
        (EvenHorizontalLayout(),
         (0, 0, 0, 200, 200, 398),
         (1, 200, 200)),
        (EvenHorizontalLayout(),
         (0, 0, 0, 400, 200, 598),
         (2, 200, 200)),
        (EvenVerticalLayout(),
         (0, 0, 0, 0, 198, 200),
         (0, 200, 200)),
        (EvenVerticalLayout(),
         (0, 0, 200, 0, 398, 200),
         (1, 200, 200)),
        (EvenVerticalLayout(),
         (0, 0, 400, 0, 598, 200),
         (2, 200, 200)),
    ]
    for layout, expected, params in cases:
        yield _verify_layout_commit, layout, expected, params


def _verify_layout_commit(layout, expected, params):
    pad_mock = Mock()
    eq_(None, layout.commit(pad_mock, *params))
    pad_mock.noutrefresh.assert_called_once_with(*expected)


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


@patch('rolex.curses')
def test_watch_remove_pane(curses_mock):
    cmd1 = Command('test1', 1, Mock())
    cmd2 = Command('test2', 2, Mock())
    watch = Watch(Mock(height=20, width=80), Mock(), [cmd1, cmd2], Mock())

    eq_(2, len(watch.panes))
    eq_(2, len(watch.pane_map))
    eq_(2, len(watch.commands))
    eq_(True, 0 in watch.pane_map)
    eq_(True, 1 in watch.pane_map)
    eq_(False, watch.panes[1].selected)
    eq_(cmd2, watch.pane_map[1])

    watch.remove_pane(watch.panes[0])

    eq_(1, len(watch.panes))
    eq_(1, len(watch.pane_map))
    eq_(1, len(watch.commands))
    eq_(True, 0 in watch.pane_map)
    eq_(False, 1 in watch.pane_map)
    eq_(cmd2, watch.pane_map[0])
    eq_(True, watch.panes[0].selected)


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
