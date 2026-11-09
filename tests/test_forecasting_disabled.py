"""Price forecasting stays OFF. The ML sufficiency report may say it is not justified, but no route, template or
frontend module may expose a forecast, and the report must never claim forecasting is justified on thin history.
The thresholds are the ones in watchtower/ml/sufficiency.py (90 distinct days, 30 listings with 30 observations)."""
import re
from pathlib import Path

from shopping.web.app import create_app
from watchtower.ml import sufficiency

ROOT = Path(__file__).resolve().parents[1]


def test_no_route_exposes_a_forecast(db):
    paths = [getattr(r, "path", "") for r in create_app(db).routes]
    assert not [p for p in paths if "forecast" in p.lower()]


def test_no_template_or_frontend_module_offers_a_forecast():
    sources = list((ROOT / "shopping" / "web" / "templates").rglob("*.html")) + list((ROOT / "frontend" / "src").rglob("*.ts"))
    hits = [p.relative_to(ROOT).as_posix() for p in sources if re.search(r"forecast", p.read_text(encoding="utf-8"), re.I)]
    assert hits == []


def test_sufficiency_refuses_forecasting_on_thin_history(db):
    with db.session() as s:
        rep = sufficiency.sufficiency_report(s)
    assert rep["justified"]["price_forecasting"] is False
    assert "forecasting is NOT justified" in rep["verdict"]
