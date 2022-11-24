# From https://stackoverflow.com/questions/7370801/how-do-i-measure-elapsed-time-in-python
from functools import wraps
from time import perf_counter
from typing import Any, Callable, Optional, TypeVar, cast

F = TypeVar("F", bound=Callable[..., Any])


def timer(prefix: Optional[str] = None, precision: int = 7) -> Callable[[F], F]:
    """Use as a decorator to time the execution of any function.

    Args:
        prefix: String to print before the time taken.
            Default is the name of the function.
        precision: How many decimals to include in the seconds value.

    Examples:
        >>> @timer()
        ... def foo(x):
        ...     return x
        >>> foo(123)
        foo: 0.000...s
        123
        >>> @timer("Time taken: ", 2)
        ... def foo(x):
        ...     return x
        >>> foo(123)
        Time taken: 0.00s
        123

    """
    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal prefix
            prefix = prefix if prefix is not None else f"{func.__name__}: "
            start = perf_counter()
            result = func(*args, **kwargs)
            end = perf_counter()
            print(f"{prefix}{end - start:.{precision}f}s")

            return result

        return cast(F, wrapper)

    return decorator
