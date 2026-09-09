"""Small OFF-DEVICE evaluator for the Kode subset used by this preset.

This tests generated expressions and renders reference previews. It is NOT the
KWGT engine and cannot certify Android imports, scheduling, or native rendering.
"""

import json
import math
import re
from datetime import datetime

TOKENS = re.compile(
    r'\s*("(?:\\.|[^"\\])*"|[0-9]+(?:\.[0-9]+)?|[A-Za-z_#][A-Za-z_0-9#]*|!=|>=|<=|[()+*/%,=<>|&-])'
)
PRECEDENCE = {
    "|": 1,
    "&": 2,
    "=": 3,
    "!=": 3,
    ">": 3,
    "<": 3,
    ">=": 3,
    "<=": 3,
    "+": 4,
    "-": 4,
    "*": 5,
    "/": 5,
    "%": 5,
}


def stringify(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def numeric(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


class Parser:
    def __init__(self, expression):
        self.tokens = []
        cursor = 0
        while cursor < len(expression.rstrip()):
            match = TOKENS.match(expression, cursor)
            if not match:
                raise ValueError(f"Unsupported token at {expression[cursor:]}")
            self.tokens.append(match[1])
            cursor = match.end()
        self.i = 0

    def pop(self):
        token = self.tokens[self.i]
        self.i += 1
        return token

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def expect(self, token):
        actual = self.pop()
        if actual != token:
            raise ValueError(f"Expected {token}, got {actual}")

    def expression(self, minimum=0):
        token = self.pop()
        if token == "(":
            node = self.expression()
            self.expect(")")
        elif token == "-":
            node = ("neg", self.expression(6))
        elif token.startswith('"'):
            node = ("literal", json.loads(token))
        elif token[0].isdigit():
            node = ("literal", float(token))
        elif self.peek() == "(":
            self.pop()
            args = []
            if self.peek() != ")":
                while True:
                    args.append(self.expression())
                    if self.peek() != ",":
                        break
                    self.pop()
            self.expect(")")
            node = ("call", token, args)
        else:
            node = ("name", token)
        while self.peek() in PRECEDENCE and PRECEDENCE[self.peek()] >= minimum:
            op = self.pop()
            node = ("op", op, node, self.expression(PRECEDENCE[op] + 1))
        return node

    def parse(self):
        tree = self.expression()
        if self.i != len(self.tokens):
            raise ValueError(f"Unconsumed tokens {self.tokens[self.i :]}")
        return tree


def fragments(value):
    """Split mixed Kode/text while preserving dollars inside quoted regexes."""
    cursor = 0
    while cursor < len(value):
        start = value.find("$", cursor)
        if start == -1:
            yield False, value[cursor:]
            return
        yield False, value[cursor:start]
        i, quote, escaped = start + 1, False, False
        while i < len(value):
            char = value[i]
            if char == '"' and not escaped:
                quote = not quote
            if char == "$" and not quote:
                break
            escaped = char == "\\" and not escaped
            i += 1
        if i == len(value):
            raise ValueError("Unterminated Kode expression")
        yield True, value[start + 1 : i]
        cursor = i + 1


class Context:
    def __init__(self, spec, values=None, now=1788998400, local=None, system=None):
        self.spec = spec
        self.values = values or {}
        self.now = now
        self.local = local or {}
        self.active = set()
        self.system = {"rwidth": 720, "rheight": 600, "darkmode": 0, **(system or {})}

    def global_value(self, name):
        if name in self.values:
            return self.values[name]
        if name not in self.spec:
            raise KeyError(f"Missing global {name}")
        if name in self.active:
            raise ValueError(f"Cyclic global {name}")
        self.active.add(name)
        try:
            item = self.spec[name]
            value = item.get("global_formula", item["value"])
            return self.render(value) if isinstance(value, str) else value
        finally:
            self.active.remove(name)

    def render(self, value):
        return "".join(
            stringify(self.eval(Parser(part).parse())) if is_code else part
            for is_code, part in fragments(value)
        )

    def eval(self, node):
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "name":
            return self.local.get(node[1], "") if node[1].startswith("#") else node[1]
        if kind == "neg":
            return -float(self.eval(node[1]))
        if kind == "op":
            op, a, b = node[1], self.eval(node[2]), self.eval(node[3])
            x, y = numeric(a), numeric(b)
            if op in ("=", "!=", ">", "<", ">=", "<="):
                a, b = (
                    (x, y)
                    if x is not None and y is not None
                    else (stringify(a), stringify(b))
                )
                return {
                    "=": lambda: a == b,
                    "!=": lambda: a != b,
                    ">": lambda: a > b,
                    "<": lambda: a < b,
                    ">=": lambda: a >= b,
                    "<=": lambda: a <= b,
                }[op]()
            if op == "+" and (x is None or y is None):
                return stringify(a) + stringify(b)
            if op in ("&", "|"):
                return (bool(x) and bool(y)) if op == "&" else (bool(x) or bool(y))
            return {
                "+": lambda: x + y,
                "-": lambda: x - y,
                "*": lambda: x * y,
                "/": lambda: x / y,
                "%": lambda: x % y,
            }[op]()
        name, arguments = node[1:]
        if name == "if":
            for i in range(0, len(arguments) - 1, 2):
                value = self.eval(arguments[i])
                if numeric(value) not in (0, None):
                    return self.eval(arguments[i + 1])
            return self.eval(arguments[-1])
        args = [self.eval(arg) for arg in arguments]
        if name == "gv":
            return self.global_value(args[0])
        if name == "si":
            return self.system[args[0]]
        if name == "df":
            if args[0] != "S":
                raise ValueError("Only epoch date format is used here")
            return (
                self.now
                if len(args) == 1
                else datetime.fromisoformat(args[1]).timestamp()
            )
        if name == "mu":
            numbers = [float(a) for a in args[1:]]
            return {
                "min": lambda: min(numbers),
                "max": lambda: max(numbers),
                "floor": lambda: math.floor(numbers[0]),
            }[args[0]]()
        if name == "tc":
            mode, value = args[:2]
            if mode == "json":
                data = json.loads(value)
                for key in args[2].lstrip(".").split("."):
                    data = data.get(key) if isinstance(data, dict) else None
                return stringify(data)
            value = stringify(value)
            if mode == "len":
                return len(value)
            if mode == "reg":
                return re.sub(args[2], args[3], value)
            if mode == "ell":
                return (
                    value
                    if len(value) <= int(args[2])
                    else value[: int(args[2]) - 1] + "…"
                )
        raise ValueError(f"Unsupported function {name} {args}")
