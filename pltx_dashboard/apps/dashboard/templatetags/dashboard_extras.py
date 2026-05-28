from django import template

register = template.Library()


@register.filter
def fmt_rupee(value):
    """Compact rupee format: ₹18.3 L, ₹1.20 Cr, ₹4.5 K — handles negatives."""
    try:
        n = float(value)
    except (ValueError, TypeError):
        return "₹0"
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1e7:
        return f"{sign}₹{n / 1e7:.2f} Cr"
    if n >= 1e5:
        return f"{sign}₹{n / 1e5:.1f} L"
    if n >= 1e3:
        return f"{sign}₹{n / 1e3:.1f} K"
    return f"{sign}₹{int(n):,}"


@register.filter
def fmt_rupee_exact(value):
    """Exact rupee format with Indian commas and 2 decimals."""
    try:
        n = float(value)
    except (ValueError, TypeError):
        return "₹0.00"
    sign = "-" if n < 0 else ""
    n = abs(n)
    return f"{sign}₹{n:,.2f}"


@register.filter
def fmt_num(value):
    try:
        n = float(value)
    except (ValueError, TypeError):
        return "0"
    if n.is_integer() or n == int(n):
        return f"{int(n):,}"
    return f"{n:,.2f}"


@register.filter
def fmt_pct(value):
    try:
        n = float(value)
    except (ValueError, TypeError):
        return "0%"
    return f"{n:.1f}%"


@register.filter
def abs_val(value):
    """Absolute value filter"""
    try:
        return abs(float(value))
    except (ValueError, TypeError):
        return 0


@register.filter
def tojson(value):
    import json
    from django.core.serializers.json import DjangoJSONEncoder

    try:
        return json.dumps(value, cls=DjangoJSONEncoder)
    except Exception:
        return "{}"


@register.filter
def multiply(value, arg):
    """Multiply value by argument"""
    try:
        return float(value) * float(arg)
    except (ValueError, TypeError):
        return 0


@register.filter
def subtract(value, arg):
    """Subtract arg from value"""
    try:
        return float(value) - float(arg)
    except (ValueError, TypeError):
        return 0
