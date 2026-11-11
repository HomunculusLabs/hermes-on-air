import argparse

from onair.cli import handle, setup_parser


def test_chat_option_classifies_chat_without_host_line(capsys):
    parser = argparse.ArgumentParser()
    setup_parser(parser)
    handle(parser.parse_args(['check-line', '--chat', 'How is the show?']))
    assert 'worthy' in capsys.readouterr().out.lower()
