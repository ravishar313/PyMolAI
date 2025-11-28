from pytest import mark
from pymol import cmd
import sys
from typing import List, Union, Any, Tuple
from pathlib import Path


def test_docstring():
    @cmd.new_command
    def func1():
        """docstring"""
    assert func1.__doc__ == "docstring"

@cmd.new_command
def func2(a: bool, b: bool):
    assert a
    assert not b

def test_bool(capsys):
    cmd.do("func2 yes, 0")
    out, err = capsys.readouterr()
    assert out == '' and err == ''

@cmd.new_command
def func3(
    nullable_point: Tuple[float, float, float],
    my_var: Union[int, float] = 10,
    my_foo: Union[int, float] = 10.0,
    extended_calculation: bool = True,
    old_style: Any = "Old behavior"
):
    assert nullable_point == (1., 2., 3.)
    assert extended_calculation
    assert isinstance(my_var, int)
    assert isinstance(my_foo, float)
    assert old_style == "Old behavior"
    
def test_generic(capsys):
    cmd.do("func3 nullable_point=1 2 3, my_foo=11.0")
    out, err = capsys.readouterr()
    assert out + err == ''

@cmd.new_command
def func4(dirname: Path = Path('.')):
    assert dirname.exists()

def test_path(capsys):
    cmd.do('func4 ..')
    cmd.do('func4')
    out, err = capsys.readouterr()
    assert out + err == ''

@cmd.new_command
def func5(old_style: Any):
    assert old_style is RuntimeError
func5(RuntimeError)

@mark.skip("This function does not works as expected")
def test_any(capsys):
    
    cmd.do("func5 RuntimeError")
    out, err = capsys.readouterr()
    assert 'AssertionError' not in out+err

@cmd.new_command
def func6(a: List):
    assert a[1] == "2"

@cmd.new_command
def func7(a: List[int]):
    assert a[1] == 2

def test_list(capsys):
    cmd.do("func6 1 2 3")
    out, err = capsys.readouterr()
    assert out + err == ''

    cmd.do("func7 1 2 3")
    out, err = capsys.readouterr()
    assert out + err == ''

@cmd.new_command
def func8(a: Tuple[str, int]):
    assert a == ("fooo", 42)

def test_tuple(capsys):
    cmd.do("func8 fooo 42")
    out, err = capsys.readouterr()
    assert out + err == ''

@cmd.new_command
def func10(a: str="sele"):
    assert a == "sele"

def test_default(capsys):
    cmd.do('func10')
    out, err = capsys.readouterr()
    assert out + err == ''

@mark.skipif(
    sys.version_info < (3, 11),
    reason="Requires StrEnum of Python 3.11+"
)
def test_str_enum(capsys):
    from enum import StrEnum
    class E(StrEnum):
        A = "a"
    @cmd.new_command
    def func11(e: E):
        assert e == E.A
        assert isinstance(e, E)
    cmd.do('func11 a')
    out, err = capsys.readouterr()
    assert out + err == ''