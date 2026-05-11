from calculator import add_numbers, subtract


def test_add_numbers() -> None:
    assert add_numbers(2, 3) == 5


def test_subtract() -> None:
    assert subtract(7, 4) == 3
