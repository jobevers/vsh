import atexit
import itertools
import os
import re
import shlex
import shutil
import subprocess
import sys
import types
import venv
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple, Union

from .__metadata__ import package_metadata
from . import terminal
from .vendored import click, colorama, toml
from .errors import InterpreterNotFound, InvalidEnvironmentError, InvalidPathError, PathNotFoundError


__all__ = ('create', 'enter', 'remove', 'show_envs', 'show_version')


colorama.init()
atexit.register(colorama.deinit)


PathString = Union[str, Path]


class VenvBuilder(venv.EnvBuilder):

    def create(self, env_dir: str, executable: Optional[PathString] = None):
        """
        Create a virtual environment in a directory.

        Args:
            env_dir (str): The target directory to create an environment in.
            executable (str, optional): path to python interpreter executable [default: sys.executable]
        """
        env_dir = str(_expand_or_absolute(Path(env_dir)))
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

    def ensure_directories(self, env_dir: str, executable: Optional[str] = None):
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

        def create_if_needed(directory):
            d = Path(directory)
            if not d.exists():
                d.mkdir(parents=True, exist_ok=True)
            elif d.is_symlink() or d.is_file():
                raise ValueError('Unable to create directory %r' % d)

        executable = executable or sys.executable

        env_path = Path(env_dir)
        if env_path.exists() and self.clear:
            self.clear_directory(env_dir)

        context = types.SimpleNamespace()
        context.env_dir = env_dir
        context.env_name = env_path.name
        prompt = self.prompt if self.prompt is not None else context.env_name
        context.prompt = f'({prompt}) '
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
        cmd = [
            context.env_exe,
            '-Im',
            'ensurepip', '--upgrade', '--default-pip'
            ]
        proc = subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE)
        if proc.returncode != 0:
            raise RuntimeError('Could not install pip')


def create(path: PathString,
           site_packages: Optional[bool] = None,
           overwrite: Optional[bool] = None,
           symlinks: Optional[bool] = None,
           upgrade: Optional[bool] = None,
           include_pip: Optional[bool] = None,
           prompt: Optional[str] = None,
           python: Optional[str] = None,
           working: Optional[str] = None,
           verbose: Union[bool, int] = None,
           interactive: Optional[bool] = None,
           dry_run: Optional[bool] = None):
    """Creates a virtual environment

    Notes: Wraps venv

    Args:
        path (str): path to virtual environment

        site_packages (bool, optional): use system packages within environment [default: False]
        overwrite (bool, optional): replace target folder [default: False]
        symlinks (bool, optional): create symbolic link to Python executable [default: True]
        upgrade (bool, optional): Upgrades existing environment with new Python executable [default: False]
        include_pip (bool, optional): Includes pip within virtualenv [default: True]
        prompt (str, optional): Modifies prompt
        python (str, optional): Version of python, python executable or path to python
        verbose (int, optional): more output [default: 0]
        working (str, optional): startup folder
        interactive (bool, optional): ask before updating system [default: False]
        dry_run (bool, optional): do not update system

    Returns:
        str: path to venv
    """
    verbose = int(max(verbose or 0, 0))
    path = _expand_or_absolute(path)
    name = path.name
    if sys.platform == 'win32':
        if symlinks:
            terminal.echo('Symlinks are unavailable on this platform.  Copying executables', verbose=verbose - 1)
        symlinks = False
    builder = _get_builder(path=path, site_packages=site_packages, overwrite=overwrite, symlinks=symlinks, upgrade=upgrade, include_pip=include_pip, prompt=prompt)
    prompt = f'Create virtual environment "{terminal.yellow(name)}" under: {terminal.green(path)}?'
    run_command = click.confirm(prompt) if interactive else True
    if run_command:
        if not dry_run:
            executable = find_interpreter(python, verbose=verbose - 1)
            builder.create(env_dir=str(path), executable=executable)
        terminal.echo(f'Created virtual environment: "{terminal.yellow(name)}" under: "{terminal.green(path)}".', verbose=verbose)
    create_config(venv_path=path, python=python, working=working, verbose=verbose - 1)
    return path


def create_config(venv_path: Path, python: Optional[str] = None, working: Optional[str] = None, verbose: Optional[int] = None):
    verbose = int(max(verbose or 0, 0))

    vsh_path = Path.home() / '.vsh'
    vsh_path.mkdir(parents=True, exist_ok=True)
    vsh_config_filepath = vsh_path.absolute() / f'{venv_path.name}.cfg'

    config = {
        'starting_path': str(Path(working or os.getcwd()).absolute()),
        'venv_path': str(venv_path),
        'python': str(python),
        }
    vsh_config_filepath.write_text(toml.dumps(config))
    terminal.echo(f'Created: {terminal.green(vsh_config_filepath)}', verbose=verbose)
    return config


def enter(path: PathString,
          command: Optional[str] = None,
          working: Optional[Path] = None,
          verbose: Union[bool, int] = 0):
    """Enters a virtual environment

    Args:
        path: path to virtual environment
        command: command to run in virtual env [default: shell]
        working: sets venv startup folder [default: vsh.cfg entry]
        verbose: Adds more information to stdout [default: 0]
    """
    verbose = int(max(verbose or 0, 0))
    terminal.echo(f'Verbose set to {terminal.green(verbose)}', verbose=verbose)
    path = _expand_or_absolute(path)
    shell = _get_shell()
    sub_command = command or shell
    python_name, python_version = _expand_interpreter_name(path / 'bin' / 'python')
    env = _update_environment(path, python_version, working)
    update_vsh_config(venv_path=path, working=working)
    venv_name = terminal.green(Path(path).name)

    # Setup the environment scripts
    vshell_config_commands = '; '.join(f'source {filepath}' for filepath in find_rc_files(venv_path=path, startup_path=working))
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


def find_existing_venv_paths(venv_homes: Optional[PathString] = None) -> Iterable[Path]:
    """Searches for virtual environments that currently exist"""
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


def find_environment_folders(path: PathString =None) -> Iterable[Tuple[str, Path]]:
    """Find virtual environment folders"""
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


def find_config_files(venv_path: Optional[Path] = None, startup_path: Optional[Path] = None) -> Iterable[Path]:
    """Find vsh config files"""
    vsh_config_filename = f'{venv_path.stem}.cfg' if venv_path and venv_path.exists else 'vsh.cfg'
    yield from _find_vsh_files(filename=vsh_config_filename, venv_path=venv_path, startup_path=startup_path)


def find_rc_files(venv_path: Optional[Path] = None, startup_path: Optional[Path] = None) -> Iterable[Path]:
    """Find .vshrc files"""
    found = []
    for p in _find_vsh_files(filename='.vshrc', venv_path=venv_path, startup_path=startup_path):
        if p.is_dir():
            for some_file in p.glob('**/*'):
                if some_file.absolute() not in found:
                    found.append(some_file.absolute())
        else:
            if p.absolute() not in found:
                found.append(p)
    for path in found:
        yield path


def find_interpreter(name_or_path: Optional[PathString] = None, verbose: Optional[int] = None) -> Path:
    """Returns the interpreter given the string"""
    verbose = int(max(verbose or 0, 0))

    if name_or_path is None:
        return Path(sys.executable)

    name, version = _expand_interpreter_name(name_or_path)

    # Maybe the path is already supplied
    if Path(name_or_path).exists():
        return Path(name_or_path)

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
        pyenv_path = Path(Path.home() / '.pyenv' / 'versions')
        for path in pyenv_path.glob('**/bin'):
            if path.parent.name.startswith(version):
                paths.append(str(path) + '/')
    interpreters = [name+version, name]
    interpreter_paths = list(map(''.join, itertools.chain(itertools.product(paths, interpreters))))

    # check for all paths and store those that actually exist
    found_paths = []
    for path in interpreter_paths:
        path = Path(path).absolute()
        terminal.echo(f'Checking for interpreter under: {path}...', end='')
        if not path.exists():
            continue
        found_paths.append(path)

    # if we have an exact match, return
    filtered_paths = {}
    for path in found_paths:
        if path.name == name+version:
            return path

        path_version = _execute_python_version(path)
        if path_version == version:
            return path

        # Prune versions that clearly do not match
        if path_version.startswith(version):
            filtered_paths.setdefault(name+path_version, path)

    # Last ditch effort... maybe we can do a partial match
    #  Assumption is that the best match will be the longest name
    #   but keep the original order.
    best_version = ''
    for version, path in reversed(sorted(filtered_paths.items())):
        if len(version) > best_version:
            best_version = version
    if best_version:
        return filtered_paths[best_version]

    raise InterpreterNotFound(version=name)


def get_venv_home(venv_path: Optional[PathString] = None, venv_name: Optional[str] = None) -> Path:
    """Returns best guess on home for virtual environments"""
    if not venv_path or not Path(venv_path).exists():
        if sys.platform in ['win32']:
                home_drive = Path(os.getenv('HOMEDRIVE'))
                venv_home = home_drive / Path(os.getenv('HOMEPATH'))
        else:
            home = Path(os.getenv('HOME'))
            venv_home = Path(os.getenv('WORKON_HOME') or home / '.virtualenvs')
    else:
        venv_home = Path(venv_path).resolve()
    if venv_name:
        venv_home = venv_home / venv_name
    return venv_home


def read_config(path: Path) -> dict:
    """Read a config file"""
    data = {}
    if path and Path(path).exists():
        with path.open() as stream:
            data = toml.loads(stream.read())
    return data


def remove(venv_path: Path, verbose: Optional[bool] = None, interactive: Optional[bool] = None, dry_run: Optional[bool] = None, check: Optional[bool] = None):
    """Remove a virtual environment

    Args:
        venv_path (str): path to virtual environment
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
    venv_path = _expand_or_absolute(venv_path)
    if not validate_environment(venv_path) and check is True:
        raise InvalidEnvironmentError(path=venv_path)
    prompt = f'Remove "{terminal.green(venv_path)}"?'
    run_command = click.confirm(prompt) == 'y' if interactive else True
    removed = False
    if run_command and not dry_run:
        if venv_path.exists():
            shutil.rmtree(str(venv_path))
            removed = True
        elif check is True:
            raise PathNotFoundError(path=venv_path)
    terminal.echo(f'Removed: {terminal.green(venv_path)}', verbose=verbose or removed or dry_run)
    remove_vsh_config(venv_path)
    return venv_path


def remove_vsh_config(venv_path: Path, verbose: Optional[bool] = None):
    """Remove a config file"""
    verbose = int(max(verbose or 0, 0))

    vsh_path = Path.home() / '.vsh'
    if vsh_path.exists():
        vsh_config_filepath = vsh_path.absolute() / f'{venv_path.name}.cfg'
        if vsh_config_filepath.exists():
            vsh_config_filepath.unlink()
            terminal.echo(f'Removed: {vsh_config_filepath}', verbose=verbose)


def show_envs(path=None):
    venv_homes = Path(path or get_venv_home())
    for name, path in find_environment_folders(path=venv_homes):
        terminal.echo(f'Found {terminal.yellow(name)} under: {terminal.yellow(path)}')


def show_version():
    terminal.echo(f"{package_metadata['name']} {package_metadata['version']}")


def update_vsh_config(venv_path: Path, python: Optional[str] = None, working: Optional[str] = None):
    vsh_path = Path.home() / '.vsh'
    vsh_config_filepath = vsh_path / f'{venv_path.name}.cfg'

    config = {}
    if vsh_config_filepath.exists():
        config = vsh_config_filepath.read_text('utf-8')
        config = toml.loads(config)

    if working:
        config['starting_path'] = str(working)

    if python:
        config['python'] = str(python)

    vsh_config_filepath.write_text(toml.dumps(config))


def validate_environment(path: PathString, check: Optional[bool] = None):
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
            # python_ver_data = python_ver_data.groupdict()
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


def validate_venv_name_and_path(venv_name: Optional[str] = None, venv_path: Optional[Path] = None) -> Tuple[str, Path]:
    if venv_path and not venv_name:
        venv_name = venv_path.name
    elif venv_name and not venv_path:
        venv_path = get_venv_home(venv_name=venv_name)
    elif venv_name and venv_path:
        venv_path = venv_path / venv_name
    if not (venv_name and venv_path):
        raise NameError(f'Invalid virtual environment: {venv_name} | {venv_path}')
    return venv_name, venv_path


# ----------------------------------------------------------------------
# Support
# ----------------------------------------------------------------------
def _execute_python_version(path: Path) -> str:
    version = ''
    proc = subprocess.run([str(path), '--version'], stderr=subprocess.PIPE, stdout=subprocess.PIPE)
    if proc.returncode == 0:
        stdout = proc.stdout.decode('utf-8').strip('\n')
        pattern = 'Python (?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<micro>[0-9]+)'
        match = re.match(pattern, stdout)
        if match:
            version = '.'.join(match.groups())
    return version


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


def _expand_interpreter_name(interpreter_name: PathString) -> Tuple[str, str]:
    path = Path(interpreter_name)
    if interpreter_name is None:
        raise InterpreterNotFound(version=interpreter_name)
    if path.exists():
        if path.is_symlink():
            path = path.resolve()
        else:
            path = path.absolute()
        interpreter_name = path.name
    pattern = '(?P<name>[a-zA-Z]*)(?P<version>[0-9.]*)'
    match = re.match(pattern, interpreter_name)
    name = interpreter_name
    version = ''
    if name not in ['None', None] and match:
        name, version = match.groups()
        version = '.'.join(str(v) for v in version.replace('.', ''))
        if not version:
            version = _execute_python_version(path)
            if not version:
                raise InterpreterNotFound(version=interpreter_name)
        if not name or name in ['p', 'py']:
            name = 'python'
    elif name in [None, 'None']:
        name = None
    return name, version


def _find_vsh_files(
        filename: Optional[str] = None,
        venv_path: Optional[Path] = None,
        startup_path: Optional[Path] = None,
        ):
    paths = []
    filename = Path('.vshrc' if filename is None else filename)
    # full list of paths to check in override order
    test_paths = [Path(p) for p in [
        '.',
        startup_path,
        venv_path,
        Path.home(),
        Path.home() / '.vsh',
        '/usr/local/etc/vsh'
        ] if p and Path(p).exists()]
    resolved_test_paths = [p.resolve() for p in test_paths]

    # add repo path if found
    try:
        cmd = shlex.split('git rev-parse --show-toplevel')
        top_of_current_repo_path = Path(subprocess.run(cmd, stderr=subprocess.PIPE, stdout=subprocess.PIPE, check=True).stdout.decode('utf-8').strip())
        if top_of_current_repo_path and top_of_current_repo_path.exists() and top_of_current_repo_path not in resolved_test_paths:
            paths.append(top_of_current_repo_path)
    except subprocess.CalledProcessError:
        pass

    # reduce if duplicates
    for path in test_paths:
        if path.resolve() not in paths:
            paths.append(path.resolve())

    # only yield paths which contain the filename
    for path in paths:
        path = path / filename.name if filename is not None else path
        if path.exists():
            yield path.absolute()


def _get_builder(
        path: Path,
        site_packages: Optional[bool] = None,
        overwrite: Optional[bool] = None,
        symlinks: Optional[bool] = None,
        upgrade: Optional[bool] = None,
        include_pip: Optional[bool] = None,
        prompt: Optional[str] = None
        ):
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


def _update_environment(
        path: Path,
        python_version: str,
        startup_path: Optional[Path] = None
        ) -> Dict[str, Any]:
    """Updates environment similar to activate from venv"""
    startup_path = Path(startup_path).absolute() if startup_path and Path(startup_path).absolute().exists() else None
    path = Path(path or _expand_or_absolute(path))
    name = path.name

    env = {k: v for k, v in os.environ.items()}
    env[package_metadata['name'].upper()] = name

    prompt_prefix = f'{terminal.magenta("vsh")} {terminal.yellow(name)} {terminal.yellow(python_version)}'

    env['VIRTUAL_ENV'] = str(path)
    sep = ':' if sys.platform not in ['win32'] else ';'
    env['PATH'] = sep.join([str(path / 'bin')] + env['PATH'].split(sep))
    shell = _get_shell()

    disable_prompt = env.get('VIRTUAL_ENV_DISABLE_PROMPT') or None
    if sys.platform in ['win32']:
        env['PROMPT'] = f'{prompt_prefix} {env.get("PROMPT")}'
    elif not disable_prompt and shell:
        if shell.name in ('bash', 'sh'):
            ps1 = env.get("PS1") or terminal.blue("\w") + '\$'
            env['PS1'] = f'{prompt_prefix} {ps1}'
        elif shell.name in ('zsh',):
            zsh_prompt = env.get('PS1') or env.get('PROMPT') or env.get('prompt')
            env['PROMPT_COMMAND'] = f'echo -en "\033]0;{name}\a"'
            env['PROMPT'] = f'{prompt_prefix} {zsh_prompt or "> "}'
        else:
            """TODO: Fix this for fish, csh, others, etc."""

    vsh_config = {}
    for config_path in find_config_files(venv_path=path, startup_path=startup_path):
        new_config = read_config(config_path)
        for k, v in new_config.items():
            vsh_config.setdefault(k, v)

    working_folder = Path(startup_path or vsh_config.get('starting_path') or os.getcwd()).expanduser().resolve()
    if working_folder.exists():
        env['CWD'] = str(working_folder)

    return env
