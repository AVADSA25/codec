"""CODEC safe arithmetic: evaluate a plain math expression without eval().

Used by the calculator skill and the chat calculator fallback. Only numbers,
+ - * / // % ** and parentheses are accepted. Results that would grow past
MAX_BITS (or a power whose exponent would make them) are refused before they
are computed, so a message such as 9**9**8 cannot freeze a service.
"""
import ast
import operator

MAX_CHARS = 300      # longer input is not a calculator question
MAX_BITS = 10_000    # about 3,000 decimal digits
MAX_DEPTH = 50       # nesting of the parsed expression

_BIN = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


class TooLarge(ValueError):
    """The expression asks for a number larger than MAX_BITS."""


def _bits(value):
    if isinstance(value, int):
        return value.bit_length()
    return 0  # floats overflow by themselves (OverflowError)


def _check(value):
    if _bits(value) > MAX_BITS:
        raise TooLarge("result too large")
    return value


def _pow(base, exp):
    if isinstance(exp, int) and isinstance(base, int):
        if exp < 0:
            return operator.pow(base, exp)  # a float, cannot grow past 1
        if abs(base) > 1 and max(base.bit_length() - 1, 1) * exp > MAX_BITS:
            raise TooLarge("power too large")
    elif abs(exp) > MAX_BITS:
        raise TooLarge("power too large")
    return operator.pow(base, exp)


def _eval(node, depth):
    if depth > MAX_DEPTH:
        raise ValueError("expression too deep")
    if isinstance(node, ast.Expression):
        return _eval(node.body, depth + 1)
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return _check(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand, depth + 1))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left = _eval(node.left, depth + 1)
        right = _eval(node.right, depth + 1)
        if isinstance(node.op, ast.Pow):
            return _check(_pow(left, right))
        return _check(_BIN[type(node.op)](left, right))
    raise ValueError(f"not allowed: {type(node).__name__}")


def safe_eval(expr):
    """Return the value of a plain arithmetic expression.

    Raises TooLarge when the answer would be too big to compute, and
    ValueError (or SyntaxError, ZeroDivisionError, OverflowError) for
    anything that is not plain arithmetic, so callers keep their own
    "cannot handle this" path.
    """
    if not isinstance(expr, str) or len(expr) > MAX_CHARS:
        raise ValueError("not a short expression")
    tree = ast.parse(expr.strip(), mode="eval")
    return _eval(tree, 0)
