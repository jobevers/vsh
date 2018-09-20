import atexit
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from .. import api, terminal
from ..vendored import click, colorama


colorama.init()
atexit.register(colorama.deinit)


@click.command(context_settings={'ignore_unknown_options': True, 'allow_interspersed_args': False})
@click.option('-c', '--copy', is_flag=True if sys.platform != 'win32' else False, help='Do not create symlinks for python')
@click.option('-C', '--create-only', is_flag=True, help='Only create venv, do not enter')
@click.option('-d', '--dry-run', is_flag=True, help='Do not make changes to the system')
@click.option('-e', '--ephemeral', is_flag=True, help='Create and remove')
@click.option('-f', '--force', is_flag=True, help='Force removal options')
@click.option('-i', '--interactive', is_flag=True, help='Run interactively')
@click.option('-l', '--ls', is_flag=True, help='Show available virtual environments')
@click.option('--no-pip', is_flag=True, help='Do not include pip')
@click.option('-o', '--overwrite', is_flag=True, help='Recreate venv')
@click.option('--path', metavar='PATH', help='Path to virtual environment', type=Path)
@click.option('-p', '--python', metavar='VERSION', help='Python version to use')
@click.option('-r', '--remove', is_flag=True, help='Remove virtual environment')
@click.option('-u', '--upgrade', is_flag=True, help='Upgrades to latest python version')
@click.option('-v', '--verbose', count=True, help='More output')
@click.option('-V', '--version', is_flag=True, help='Show version and exit')
@click.option('-w', '--working', metavar='PATH', default=None, help=f'Default startup PATH when entering virtual environment [{os.getcwd()}]', type=Path)
@click.option('--shell-completion', is_flag=True, help='Show shell completion code')
@click.argument('name', metavar='VENV_NAME', required=False)
@click.argument('command', required=False, nargs=-1)
@click.pass_context
def vsh(ctx, copy, create_only, dry_run, ephemeral, force, interactive, shell_completion, ls, no_pip, overwrite, path, python, remove, upgrade, verbose, version, name, command, working):
    if shell_completion:
        subprocess.run('source vsh', shell=True)
        sys.exit(0)

    name, path = api.validate_venv_name_and_path(venv_name=name, venv_path=path)
    verbose = verbose or 0
    return_code = 0
    if version:
        api.show_version()
        sys.exit(return_code)

    if ls:
        api.show_envs()
        sys.exit(return_code)

    if path and name:
        # favor path over name
        command = list(command)
        name = Path(path).name

    if not (path or name):
        terminal.echo(vsh.get_help(ctx))
        terminal.echo('\nERROR: A name or path must be provided.')
        sys.tracebacklimit = 0
        return_code = 1
        sys.exit(return_code)

    if not path:
        path = api.get_venv_home(venv_name=name)

    # Determine if an environment already exists
    exists = api.validate_environment(path)

    if not command and not remove:
        command = os.getenv('SHELL')

    if exists and upgrade:
        pass

    elif not exists and not remove:
        api.create(path, include_pip=not no_pip, overwrite=overwrite, symlinks=not copy, python=python, working=working, verbose=verbose)
        if ephemeral:
            remove = True

    if (sys.platform in ['win32'] or command) and not create_only:
        return_code = api.enter(path, command, verbose=max(verbose - 1, 0), working=working)

    if ephemeral and not (force or remove):
        msg = textwrap.dedent(f"""\
        Virtual environment "{terminal.green(name)}" existed previously.
        To remove manually, run:
        
            {terminal.blue(f"vsh -r {name}")}
         
        {terminal.yellow("WARNING: Ephemeral option ignored. Aborting removal.")}
        """)
        terminal.echo(msg)

    if remove:
        api.remove(path, verbose=verbose, interactive=interactive, dry_run=dry_run)

    sys.tracebacklimit = 0
    sys.exit(return_code)


if __name__ == '__main__':
    vsh()
