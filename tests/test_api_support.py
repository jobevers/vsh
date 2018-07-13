import pytest


@pytest.mark.parametrize('data', [
    {'name': '37', 'expected': 'python3.7'},
    {'name': 'pypy35', 'expected': 'pypy3.5'},
    {'name': 'pypy6', 'expected': 'pypy6'},
    {'name': 'p37', 'expected': 'python3.7'},
    {'name': 'py37', 'expected': 'python3.7'},
    {'name': '375', 'expected': 'python3.7.5'},

    ])
def test_expand_interpreter_name(data):
    from vsh.api import _expand_interpreter_name

    name = data['name']
    expected = data['expected']

    assert _expand_interpreter_name(name) == expected
