# Rolex

`rolex` is a better `watch(1)`. It can run a command periodically, displaying
its output in the full width and height of the terminal, but it can also:

* run multiple commands in split panes, independently configured, that adjust
  to changes in terminal size
* dynamically change the time between runs of individual commands
* dynamically add and remove new commands
* pause and unpause command runs
* highlight differences in a command's output from the previous run ("diff
  last" mode) or against the output at a particular point in time ("diff mark"
  mode)
* highlight matches of a pattern in a command's output
* manually rerun a command


# Quick start

`rolex` is implemented in Python, in a single executable script, and has no
dependencies beyond the Python standard library.

    $ wget https://github.com/hut8labs/rolex/raw/master/rolex
    $ chmod +x rolex
    $ ./rolex command1 arg1 arg2 -- command2 arg1

Pressing "?" or "h" in `rolex` will display keybindings in `less` (or
`$PAGER`).


# A screenshot

![Rolex Screenshot](https://github.com/hut8labs/rolex/blob/master/doc/rolex.png?raw=true)


# Todo

Some work in progress and feature ideas:

* rewinding command output
* pane resizing/moving
* 256-color support
* graph mode
* friendly time ("2h23m ago")
* better configuration
* pausing/resuming individual commands
