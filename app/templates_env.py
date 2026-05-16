import json
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fromjson"] = json.loads


def _cbs_value(value):
    """Render CBS stat; sentinel -99997 becomes an em-dash."""
    if value is None or value == -99997:
        return "—"
    return value


templates.env.filters["cbs_value"] = _cbs_value
