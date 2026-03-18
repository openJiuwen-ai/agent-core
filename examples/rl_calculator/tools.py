"""
Calculator tool definition.
"""

from openjiuwen.core.common.logging import tool_logger
from openjiuwen.core.foundation.tool import tool


@tool(
    name="calculator",
    description="Perform arithmetic calculations, simplify algebraic expressions, and solve equations.",
)
def calculator(expression: str) -> str:
    """Evaluate a math expression and return the result.

    Automatically detects the expression type:
    - Pure arithmetic (e.g. '2+3') -> numeric result
    - Equation with '=' (e.g. '2*x+3=7') -> solve for variables
    - Algebraic expression with variables (e.g. '2*(x^2-3)') -> expand and simplify
    """
    try:
        import sympy
        from sympy.parsing.sympy_parser import (
            convert_xor,
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )

        transformations = standard_transformations + (
            implicit_multiplication_application,
            convert_xor,
        )
        local_dict = {c: sympy.Symbol(c) for c in "abcdefghijklmnopqrstuvwxyz"}
        local_dict["sqrt"] = sympy.sqrt

        expr_clean = expression.strip().replace("^", "**")

        if "=" in expr_clean and "==" not in expr_clean:
            lhs_s, rhs_s = expr_clean.split("=", 1)
            lhs = parse_expr(
                lhs_s.strip(),
                local_dict=local_dict,
                transformations=transformations,
            )
            rhs = parse_expr(
                rhs_s.strip(),
                local_dict=local_dict,
                transformations=transformations,
            )
            eq = sympy.Eq(lhs, rhs)
            free_vars = eq.free_symbols
            var = (
                sorted(free_vars, key=lambda s: s.name)[0]
                if free_vars
                else sympy.Symbol("x")
            )
            result = sympy.solve(eq, var)
            if len(free_vars) > 1:
                return str(result)
            if len(result) == 1:
                return f"{var} = {result[0]}"
            return ", ".join(f"{var} = {r}" for r in result)

        try:
            result = eval(expr_clean)  # noqa: S307
            return str(result)
        except Exception as e:
            tool_logger.debug("eval failed for expression %r, falling back to sympy: %s", expr_clean, e)

        parsed = parse_expr(
            expr_clean, local_dict=local_dict, transformations=transformations
        )
        if parsed.free_symbols:
            expanded = sympy.expand(parsed)
            simplified = sympy.simplify(expanded)
            return str(simplified)
        else:
            return str(parsed.evalf())

    except Exception as e:
        tool_logger.exception("Calculator failed for expression %r: %s", expression, e)
        return f"Error: {e}"
