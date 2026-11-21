"""One foreground execution controller for both /resume modes."""
import argparse
import shlex


def resume_options(text):
    # Raise ValueError, never exit the interactive session on a bad argument.
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError('Use /resume [positive steps] OR /resume --continuous: ' + message)
    parser = Parser(add_help=False)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('steps', nargs='?', type=int)
    group.add_argument('--continuous', action='store_true')
    args = parser.parse_args(shlex.split(text))
    if args.steps is not None and args.steps < 1:
        raise ValueError('steps must be positive')
    return args.steps or 30, args.continuous


def resume(engine, steps=30, continuous=False):
    return engine.run(steps, continuous=continuous)
