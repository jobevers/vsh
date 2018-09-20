import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union

from .vendored import toml

PathString = Union[str, Path]


@dataclass
class Config:
    venv_name: Optional[str] = None
    venv_path: Optional[Path] = None
    config_path: Optional[Path] = None
    starting_path: Optional[Path] = None
    interpreter: Optional[PathString] = None

    def __post_init__(self):
        if self.config_path and self.config_path.resolve().exists():
            self.load(self.config_path)

        if self.venv_name is None and self.venv_path:
            self.venv_name = self.venv_path.name

        elif self.venv_path is None and self.venv_name:
            HOME = Path.home()
            WORKON_HOME = os.getenv('WORKON_HOME')
            home = Path(WORKON_HOME or HOME)
            self.venv_path = home / '.virtualenvs' / self.venv_name.name

        if self.starting_path is None:
            self.starting_path = Path(os.getcwd())

        if not self.config_path and self.venv_name:
            self.config_path = Path.home() / '.vsh' / f'{self.venv_name}.cfg'

        if self.interpreter is None:
            self.interpreter = Path(sys.executable)
        else:
            self.interpreter = self._find_interpreter_path(self.interpreter)

    def dump(self, config_path: Path):
        data = {
            field_name: str(getattr(self, field_name))
            for field_name in self.__annotations__.keys()
            if getattr(self, field_name) is not None
            }
        if data:
            toml.dump(data, config_path.open('w'))

    def load(self, config_path: Optional[Path] = None):
        config_path = config_path or self.config_path
        if config_path.exists():
            data = self.parse(config_path.read_text(encoding='utf-8'))
            for field_name, field_type in self.__annotations__.items():
                if field_type != Path:
                    value = data.get(field_name) or getattr(self, field_name)
                else:
                    value = data.get(field_name)
                    default = getattr(self, field_name)
                    value = self._load_path(value, default)
                if value is not None:
                    setattr(self, field_name, value)
        return self

    @staticmethod
    def _find_interpreter_path(interpreter: str) -> Path:
        default_interpreter = Path(sys.executable)


        return default_interpreter

    @staticmethod
    def _load_path(path: str, default: Path):
        path = Path(path)
        if path.resolve().exists():
            return path
        else:
            return default

    @staticmethod
    def parse(raw_data: str) -> Dict:
        data = toml.loads(raw_data)
        return data
