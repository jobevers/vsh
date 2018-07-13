from .vendored.colorama import Fore, Style
import typing

from .vendored import click


def blue(msg: typing.Any):
    msg = Fore.BLUE + str(msg) + Style.RESET_ALL
    return msg


def green(msg: typing.Any):
    msg = Fore.GREEN + str(msg) + Style.RESET_ALL
    return msg


def magenta(msg: typing.Any):
    msg = Fore.MAGENTA + str(msg) + Style.RESET_ALL
    return msg


def red(msg: typing.Any):
    msg = Fore.RED + str(msg) + Style.RESET_ALL
    return msg


def yellow(msg: typing.Any):
    msg = Fore.YELLOW + str(msg) + Style.RESET_ALL
    return msg


def echo(message, verbose=None):
    if verbose or (verbose is None):
        click.echo(message)
