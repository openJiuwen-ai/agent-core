#contains intentional type errors

x: int = "not_an_integer"  # Error: str not assignable to int


def add(a: int, b: int) -> int:
    return a + b


result: str = add(1, 2)    # Error: int not assignable to str
