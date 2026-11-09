"""One place that turns a BeautifulSoup attribute into text. Single-valued attributes are strings; multi-valued ones (such as
class) come back as lists, which are joined so callers always get a string or None."""


def attr_text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value)
