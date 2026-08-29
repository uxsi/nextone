from api import hello


def test_greeting():
    result = hello("Alice")
    assert result == "Hello, Alice!"


def test_empty_name():
    result = hello("")
    assert result == "Hello, !"
