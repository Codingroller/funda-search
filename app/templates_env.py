import json
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fromjson"] = json.loads


def _cbs_value(value):
    """Render CBS stat; sentinel -99997 becomes an em-dash."""
    if value is None or value == -99997:
        return "—"
    return value


def _format_price(value):
    """Format an integer as Dutch-style number: 670000 → '670.000'."""
    try:
        return f"{int(value):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(value)


templates.env.filters["cbs_value"] = _cbs_value
templates.env.filters["format_price"] = _format_price
