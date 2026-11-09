"""Query-parameter types shared by the page and API route modules. A price limit is a decimal major-unit number,
bounded so a typo cannot ask for an absurd range; text parameters are length-capped."""
from typing import Annotated

from fastapi import Query

Money = Annotated[float | None, Query(ge=0, le=1e9, allow_inf_nan=False)]
Text = Annotated[str, Query(max_length=200)]
