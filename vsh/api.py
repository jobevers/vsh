import atexit
import itertools
import os
import re
import shlex
import shutil
import subprocess
import sys
import types
import typing
import venv
from pathlib import Path

from .__metadata__ import package_metadata
from . import terminal
from .vendored import click, colorama, toml
from .errors import InterpreterNotFound, InvalidEnvironmentError, InvalidPathError, PathNotFoundError


__all__ = ('create', 'enter', 'remove', 'show_envs', 'show_version')


colorama.init()
atexit.register(colorama.deinit)


PathString = typing.Union[str, Path]


class VenvBuilder(venv.EnvBuilder):

    def create(self, env_dir, executable=None):
        """
        Create a virtual environment in a directory.

        Args:
            env_dir (str): The target directory to create an environment in.
            executable (str, optional): path to python interpreter executable [default: sys.executable]
        """
        env_dir = os.path.abspath(env_dir)
        context = self.ensure_directories(env_dir=env_dir, executable=executable)
        # See issue 24875. We need system_site_packages to be False
        # until after pip is installed.
        true_system_site_packages = self.system_site_packages
        self.system_site_packages = False
        self.create_configuration(context)
        self.setup_python(context)
        if self.with_pip:
            self._setup_pip(context)
        if not self.upgrade:
            self.setup_scripts(context)
            self.post_setup(context)
        if true_system_site_packages:
            # We had set it to False before, now
            # restore it and rewrite the configuration
            self.system_site_packages = True
        self.create_configuration(context)

    def ensure_directories(self, env_dir, executable=None):
        """
        Create the directories for the environment.
        Returns a context object which holds paths in the environment,
        for use by subsequent logic.

        Args:
            env_dir (str): path to environment
            executable (str, optional): path to python interpreter executable [default: sys.executable]

        Returns:
            types.SimpleNamespace: context
        """

        def create_if_needed(d):
            if not os.path.exists(d):
                os.makedirs(d)
            elif os.path.islink(d) or os.path.isfile(d):
                raise ValueError('Unable to create directory %r' % d)

        executable = executable or sys.executable
        if os.path.exists(env_dir) and self.clear:
            self.clear_directory(env_dir)
        context = types.SimpleNamespace()
        context.env_dir = env_dir
        context.env_name = os.path.split(env_dir)[1]
        prompt = self.prompt if self.prompt is not None else context.env_name
        context.prompt = '(%s) ' % prompt
        create_if_needed(env_dir)
        dirname, exename = os.path.split(os.path.abspath(executable))
        context.executable = executable
        context.python_dir = dirname
        context.python_exe = exename
        if sys.platform == 'win32':
            binname = 'Scripts'
            incpath = 'Include'
            libpath = os.path.join(env_dir, 'Lib', 'site-packages')
        else:
            binname = 'bin'
            incpath = 'include'
            libpath = os.path.join(env_dir, 'lib', exename, 'site-packages')
        context.inc_path = path = os.path.join(env_dir, incpath)
        create_if_needed(path)
        create_if_needed(libpath)
        # Issue 21197: create lib64 as a symlink to lib on 64-bit non-OS X POSIX
        if (sys.maxsize > 2**32) and (os.name == 'posix') and (sys.platform != 'darwin'):
            link_path = os.path.join(env_dir, 'lib64')
            if not os.path.exists(link_path):   # Issue #21643
                os.symlink('lib', link_path)
        context.bin_path = binpath = os.path.join(env_dir, binname)
        context.bin_name = binname
        context.env_exe = os.path.join(binpath, exename)
        create_if_needed(binpath)
        return context

    def _setup_pip(self, context):
        """Installs or upgrades pip in a virtual environment"""
        # We run ensurepip in isolated mode to avoid side effects from
        # environment vars, the current directory and anything else
        # intended for the global Python environment
        # Originally -Im, but -Esm works on both python2 and python3
        cmd = [context.env_exe, '-Esm', 'ensurepip', '--upgrade',
                                                     '--default-pip']
        subprocess.check_output(cmd, stderr=subprocess.STDOUT)


def create(path: PathString,
           site_packages: bool = False,
           overwrite: bool = False,
           symlinks: bool = False,
           upgrade: bool = False,
           include_pip: bool = False,
           prompt: str = '',
           python: str = '',
           verbose: typing.Union[bool, int] = False,
           interactive: bool = False,
           dry_run: bool = False):
    """Creates a virtual environment

    Notes: Wraps python base package venv

    Examples:
        To create a basic Virtual Environment, simply run create with
        a specific path.  This will be a bare-bones environment and will
        spend the effort to copy all of the required binaries and
        libraries from the host system.

        >>> from vsh.api import create
        >>> create('/tmp/new-venv')
        ...

        To speed up the process and to simply symlink, the following
        may be used.  ***Note that Windows cannot create symlinks.***
        >>> from vsh.api import create
        >>> create('/tmp/new-venv', symlinks=True)
        ...

        To specify the prompt used when entering the virtual environment
        the `prompt` argument can be updated.
        >>> from vsh.api import create
        >>> create('/tmp/new-venv', symlinks=True, prompt='(new-venv)')
        ...

        To specify a different version of python other than the
        currently running python, use the `python` argument.  ***Note:
        the python specified must already exist on the system.***
        >>> from vsh.api import create
        >>> create('/tmp/new-venv', symlinks=True, python='3.6.5')
        ...

    Args:
        path: path to virtual environment

        site_packages: use system packages within environment
        overwrite: replace target folder [default: False]
        symlinks: create symbolic link to Python executable [default: True]
        upgrade: Upgrades existing environment with new Python executable [default: False]
        include_pip: Includes pip within virtualenv [default: True]
        prompt: Modifies prompt
        python: Version of python, python executable or path to python
        verbose: more output [default: 0]
        interactive: ask before updating system [default: False]
        dry_run: do not update system

    Returns:
        Path to created virtual environment
    """
    verbose = int(max(verbose or 0, 0))
    path = _expand_or_absolute(path)
    name = path.name
    if sys.platform == 'win32':
        if symlinks:
            terminal.echo(f'{terminal.yellow("Warning")}: Symlinks are unavailable on this platform.')
        symlinks = False
    builder = _get_builder(path=path, site_packages=site_packages, overwrite=overwrite, symlinks=symlinks, upgrade=upgrade, include_pip=include_pip, prompt=prompt)
    prompt = f'Create virtual environment "{terminal.yellow(name)}" under: {terminal.green(path)}?'
    run_command = click.confirm(prompt) if interactive else True
    if run_command:
        if not dry_run:
            executable = find_interpreter(python)
            if not executable:
                raise InterpreterNotFound(version=python)
            builder.create(env_dir=str(path), executable=executable)
        terminal.echo(f'Created virtual environment: "{terminal.yellow(name)}" under: "{terminal.green(path)}".')
    create_vsh_config(venv_path=path, python=python)
    return path


def create_vsh_config(venv_path: PathString, python: str = None):
    config = {}
    config['starting_path'] = str(Path(os.getcwd()).absolute())
    config['venv_path'] = str(venv_path)
    config['python'] = python
    config_filename = venv_path / 'vsh.cfg'
    with config_filename.open(mode='w') as stream:
        data = toml.dumps(config)
        stream.write(data)
    return config


def enter(path: PathString, command: str = None, verbose: bool = None):
    """Enters a virtual environment

    Args:
        path (str): path to virtual environment
        command (tuple|list|str, optional): command to run in virtual env [default: shell]
        verbose (int, optional): Adds more information to stdout
    """
    verbose = int(max(verbose or 0, 0))
    terminal.echo(f'Verbose set to {terminal.green(verbose)}', verbose=verbose)
    path = _expand_or_absolute(path)
    shell = _get_shell()
    sub_command = command or shell
    env = _update_environment(path)
    venv_name = terminal.green(Path(path).name)

    # Setup the environment scripts
    vshell_config_commands = '; '.join(f'source {filepath}' for filepath in find_vsh_rc_files(venv_path=path))
    if isinstance(sub_command, (list, tuple)):
        sub_command = " ".join(sub_command)
    if vshell_config_commands:
        sub_command = f'{vshell_config_commands}; {sub_command}'
    cmd_display = terminal.green(sub_command)
    if shell and Path(shell).name in ['bash', 'zsh']:
        sub_command = f'{shell} -i -c \"{sub_command}\"'
        cmd_display = f'{shell} -i -c \"{cmd_display}\"'
    elif not shell:
        if sub_command:
            sub_command = f'cmd /K {sub_command}'
            cmd_display = sub_command.format(command=cmd_display)
        else:
            sub_command = f'cmd'
            cmd_display = sub_command
    terminal.echo(f'Running command in "{terminal.green(venv_name)}": {cmd_display}', verbose=max(verbose - 1, 0))

    # Activate and run
    colorama.deinit()
    return_code = subprocess.call(sub_command, shell=True, env=env, cwd=env['CWD'], universal_newlines=True)
    colorama.init()
    rc = terminal.green(return_code) if return_code == 0 else terminal.red(return_code)
    terminal.echo(f'Command return code: {rc}', verbose=max(verbose - 1, 0))
    return return_code


def find_existing_venv_paths(venv_homes: PathString = None) -> Path:
    venv_homes = Path(venv_homes or get_venv_home())
    standard_path = ['include', 'lib', 'bin']

    for venvs_home in venv_homes.glob('*/*'):
        if not venvs_home.is_dir():
            continue
        for path in os.scandir(venvs_home):
            if Path(path).is_dir():
                if Path(path).stem.startswith('-'):
                    continue
                if Path(path).stem not in standard_path:
                    yield Path(path).stem


def find_environment_folders(path: PathString =None) -> typing.Tuple[str, Path]:
    path = str(Path(path or get_venv_home()))
    for root, directories, files in os.walk(str(path)):
        root = Path(root)
        found = []
        for index, name in enumerate(directories):
            directory = root / name
            if not validate_environment(directory):
                continue
            yield name, directory
            found.append(name)
        # This makes the search "fast" by skipping out on folders
        #  that do not need to be searched because they have already
        #  been identified as valid environments
        directories[:] = [d for d in directories if d not in found]


def find_vsh_config_files(venv_path=None):
    found = list(_find_vsh_files(filename='vsh.cfg', venv_path=venv_path))
    yield from reversed(found)


def find_vsh_rc_files(venv_path=None):
    for p in _find_vsh_files(filename='.vshrc', venv_path=venv_path):
        if p.is_dir():
            for some_file in p.glob('**/*'):
                yield some_file.absolute()
        else:
            yield p


def find_interpreter(name_or_path: PathString = None) -> Path:
    """Returns the interpreter given the string"""
    if name_or_path is None:
        return Path(sys.executable)

    name, version = _expand_interpreter_name(name_or_path)

    # Maybe the path is already supplied
    if Path(name).exists():
        return Path(name)

    # Guess path
    env_paths = os.getenv('PATH').split(':')
    paths = [path + os.path.sep if not path.endswith(os.path.sep) else '' for path in env_paths]
    # maybe it's a pyenv install?
    if sys.platform in ['darwin']:
        # maybe its a cellar install
        cellar_path = Path('/usr/local/Cellar/python')
        paths += [
            str(path) + os.path.sep for path in cellar_path.glob('*/bin')
            ]
    if sys.platform in ['darwin', 'linux']:
        paths = [
            str(Path(os.getenv('HOME')) / '.pyenv' / 'versions' / version / 'bin') + '/'
            ] + paths
    interpreters = [name]
    interpreter_paths = list(map(''.join, itertools.chain(itertools.product(paths, interpreters))))
    for path in interpreter_paths:
        path = Path(path).absolute()

        if path.exists():
            return path
        else:
            print(f'skipping {path}')
    raise InterpreterNotFound(version=name)


def get_venv_home(venv_path: PathString = None, venv_name: str = None) -> Path:
    """Returns best guess on home for virtual environments"""
    if not venv_path or not Path(venv_path).exists():
        if sys.platform in ['win32']:
                home_drive = Path(os.getenv('HOMEDRIVE'))
                venv_home = home_drive / Path(os.getenv('HOMEPATH'))
        else:
            home = Path(os.getenv('HOME'))
            venv_home = Path(os.getenv('WORKON_HOME') or home / '.virtualenvs')
    else:
        venv_home = str(venv_path)
    if venv_name:
        venv_home = venv_home / venv_name
    return venv_home


def read_config_file(path: PathString) -> dict:
    data = {}
    if path and Path(path).exists():
        with path.open() as stream:
            data = toml.loads(stream.read())
    return data


def remove(path: PathString, verbose: bool = None, interactive: bool = None, dry_run: bool = None, check: bool = None):
    """Remove a virtual environment

    Args:
        path (str): path to virtual environment
        verbose (int, optional): more output [default: 0]
        interactive (bool, optional): ask before updating system [default: False]
        dry_run (bool, optional): do not update system
        check (bool, optional): Raises PathNotFoundError if True and path isn't found [default: False]

    Raises:
        PathNotFoundError:  when check is True and path is not found

    Returns:
        str: folder path removed
    """
    verbose = int(max(verbose or 0, 0))
    check = False if check is None else check
    path = _expand_or_absolute(path)
    if not validate_environment(path) and check is True:
        raise InvalidEnvironmentError(path=path)
    prompt = f'Remove "{terminal.green(path)}"?'
    run_command = click.confirm(prompt) == 'y' if interactive else True
    removed = False
    if run_command and not dry_run:
        if os.path.exists(path):
            shutil.rmtree(path)
            removed = True
        elif check is True:
            raise PathNotFoundError(path=path)
    terminal.echo(f'Removed: {terminal.green(path)}', verbose=removed or dry_run)
    return path


def show_envs(path=None):
    venv_homes = Path(path or get_venv_home())
    for name, path in find_environment_folders(path=venv_homes):
        terminal.echo(f'Found {terminal.yellow(name)} under: {terminal.yellow(path)}')


def show_version():
    terminal.echo(f"{package_metadata['name']} {package_metadata['version']}")


def validate_environment(path: PathString, check: bool = None):
    """Validates if path is a virtual environment

    Args:
        path (str): path to virtual environment
        check (bool, optional): Raise an error if path isn't valid

    Raises:
        InvalidEnvironmentError: when environment is not valid

    Returns:
        bool: True if valid virtual environment path
    """
    path = Path(path)
    valid = None
    win32 = sys.platform == 'win32'
    # Expected structure
    structure = {
        'bin': 'Scripts' if win32 else 'bin',
        'include': 'Include' if win32 else 'include',
        'lib': os.path.join('Lib', 'site-packages') if win32 else os.path.join('lib', '*', 'site-packages'),
        }
    paths = {}
    for identifier, expected_path in structure.items():
        for p in path.glob(expected_path):
            # There should only be one path that matches the glob
            paths[identifier] = p
            break
    for identifier in structure:
        if identifier not in paths:
            valid = False
            if check:
                raise InvalidEnvironmentError(f'Could not find {structure[identifier]} under {path}.')

    if valid is not False and win32:
        # TODO: Add more validation for windows environments
        valid = valid is not False and True
    elif valid is not False:
        # check for activation scripts
        activation_scripts = list(paths['bin'].glob('activate.*'))
        valid = valid is not False and len(activation_scripts) > 0
        if check and valid is False:
            raise InvalidEnvironmentError(f'Could not find activation scripts under {path}.')

        # check for python binaries
        python_name = paths['lib'].parent.name
        python_ver_data = re.search('(?P<interpreter>python|pypy)\.?(?P<major>\d+)(\.?(?P<minor>\d+))', python_name)
        if python_ver_data:
            python_ver_data = python_ver_data.groupdict()
            python_executable = paths['bin'].joinpath('python')
            python_ver_executable = paths['bin'].joinpath(python_name)
            if python_executable.exists():
                valid = valid is not False and True
            if check and valid is False:
                raise InvalidEnvironmentError(f'Could not find python executable under {path}.')
            if python_ver_executable.exists():
                valid = valid is not False and True
            if check and valid is False:
                raise InvalidEnvironmentError(f'Could not find {python_name} executable under {path}.')

    return valid


# ----------------------------------------------------------------------
# Support
# ----------------------------------------------------------------------
def _expand_or_absolute(path: Path) -> Path:
    if path:
        path = Path(path)
        if path.parts and path.parts[0].startswith('~'):
            path = path.expanduser()
        else:
            path = path.absolute()
        return path
    else:
        raise InvalidPathError(path=path)


def _expand_interpreter_name(interpreter_name: PathString) -> typing.Tuple[str, str]:
    if interpreter_name is None:
        raise InterpreterNotFound(version=interpreter_name)
    if Path(interpreter_name).exists():
        interpreter_name = Path(interpreter_name).name
    pattern = '(?P<name>[a-zA-Z]*)(?P<version>[0-9.]*)'
    match = re.match(pattern, interpreter_name)
    name = interpreter_name
    version = ''
    if name not in ['None', None] and match:
        name, version = match.groups()
        version = '.'.join(str(v) for v in version.replace('.', ''))
        if not version:
            print(interpreter_name)
            raise InterpreterNotFound(version=interpreter_name)
        if not name or name in ['p', 'py']:
            name = 'python'
    elif name in [None, 'None']:
        name = None
    return name, version


def _find_vsh_files(filename=None, venv_path=None):
    paths = []
    filename = '.vshrc' if filename is None else filename
    try:
        paths.extend([Path(p) for p in [
            '/usr/local/etc/vsh',
            os.getenv('HOME'),
            '.',
            str(venv_path),
            ] if p and Path(p).exists()])
        cmd = shlex.split('git rev-parse --show-toplevel')
        top_of_current_repo_path = Path(subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, check=True).stdout.decode('utf-8').strip())
        if top_of_current_repo_path and top_of_current_repo_path.exists():
            paths.append(top_of_current_repo_path)
    except subprocess.CalledProcessError:
        pass

    for p in paths:
        p = p / filename if filename is not None else p
        if p.exists():
            yield p.absolute()


def _get_builder(path: PathString, site_packages: bool = None, overwrite: bool = None, symlinks: bool = None, upgrade: bool = None, include_pip: bool = None, prompt: str = None):
    name = _expand_or_absolute(path).name
    builder = VenvBuilder(
        system_site_packages=False if site_packages is None else site_packages,
        clear=False if overwrite is None else overwrite,
        symlinks=True if symlinks is None else symlinks,
        upgrade=False if upgrade is None else upgrade,
        with_pip=True if include_pip is None else include_pip,
        prompt=f'({name})' if prompt is None else prompt,
        )
    return builder


def _get_shell():
    if sys.platform in ['win32']:
        shell = None
    else:
        shell = Path(os.getenv('SHELL') or '/bin/sh')
    return shell


def _update_environment(path):
    """Updates environment similar to activate from venv"""
    path = Path(path or _expand_or_absolute(path))
    name = path.name

    env = {k: v for k, v in os.environ.items()}
    env[package_metadata['name'].upper()] = name

    venv = f'{terminal.magenta("vsh")} {terminal.yellow(name)}'

    env['VIRTUAL_ENV'] = str(path)
    sep = ':' if sys.platform not in ['win32'] else ';'
    env['PATH'] = sep.join([str(path / 'bin')] + env['PATH'].split(sep))
    shell = _get_shell()
    disable_prompt = env.get('VIRTUAL_ENV_DISABLE_PROMPT') or None
    if sys.platform in ['win32']:
        env['PROMPT'] = f'{venv} {env.get("PROMPT")}'
    elif not disable_prompt and shell:
        if shell.name in ('bash', 'sh'):
            ps1 = env.get("PS1") or terminal.blue("\w") + '\$'
            env['PS1'] = f'{venv} {ps1}'
        elif shell.name in ('zsh',):
            env['PROMPT'] = f'{venv} {env.get("PROMPT") or ""}'
        else:
            """TODO: Fix this for fish, csh, others, etc."""

    vsh_config = {}
    for config_path in find_vsh_config_files(venv_path=path):
        new_config = read_config_file(config_path)
        for k, v in new_config.items():
            vsh_config.setdefault(k, v)

    working_folder = vsh_config.get('starting_path') or os.getcwd()
    if working_folder:
        env['CWD'] = working_folder

    return env
