import json
from datetime import timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

_AMS = ZoneInfo("Europe/Amsterdam")

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fromjson"] = json.loads


def _localdt(dt, fmt: str = "%d %b %H:%M") -> str:
    """Convert a naive UTC datetime to Amsterdam local time and format it."""
    if dt is None:
        return ""
    return dt.replace(tzinfo=timezone.utc).astimezone(_AMS).strftime(fmt)


templates.env.filters["localdt"] = _localdt


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
templates.env.filters["url_quote"] = lambda s: quote(str(s), safe="")
