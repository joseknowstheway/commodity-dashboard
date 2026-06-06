"""Static layout for the commodity dashboard.

`build_layout` returns the Dash component tree: a header, the control row
(commodity dropdown + date-range picker), three KPI cards, and two charts.
Component values are populated at runtime by the callbacks (Chunk 7 callbacks).
"""

from __future__ import annotations

from dash import dcc, html

# --- Simple design tokens (kept inline so the app is self-contained) -------
COLORS = {
    "bg": "#0f1720",
    "panel": "#16212e",
    "panel_alt": "#1d2a3a",
    "text": "#e6edf3",
    "muted": "#8aa0b2",
    "accent": "#4cc9f0",
    "up": "#3ddc97",
    "down": "#ff6b6b",
    "border": "#243447",
}

_CARD_STYLE = {
    "backgroundColor": COLORS["panel_alt"],
    "borderRadius": "12px",
    "padding": "18px 22px",
    "flex": "1",
    "border": f"1px solid {COLORS['border']}",
}

_LABEL_STYLE = {
    "color": COLORS["muted"],
    "fontSize": "13px",
    "textTransform": "uppercase",
    "letterSpacing": "0.06em",
    "marginBottom": "6px",
}

_VALUE_STYLE = {"color": COLORS["text"], "fontSize": "30px", "fontWeight": "700"}

_GRAPH_CONFIG = {"displayModeBar": False}


def _kpi_card(label: str, value_id: str) -> html.Div:
    """A single KPI card whose value is filled by a callback."""
    return html.Div(
        [
            html.Div(label, style=_LABEL_STYLE),
            html.Div("—", id=value_id, style=_VALUE_STYLE),
        ],
        style=_CARD_STYLE,
    )


def build_layout(
    commodities: dict,
    default_key: str,
    date_min: str | None,
    date_max: str | None,
) -> html.Div:
    """Build the full dashboard layout.

    Args:
        commodities: The COMMODITIES catalog (key -> Commodity).
        default_key: Commodity selected on first load.
        date_min/date_max: ISO date bounds for the date-range picker.
    """
    options = [{"label": c.label, "value": key} for key, c in commodities.items()]

    return html.Div(
        style={
            "backgroundColor": COLORS["bg"],
            "minHeight": "100vh",
            "padding": "28px 36px",
            "fontFamily": "Inter, system-ui, -apple-system, sans-serif",
        },
        children=[
            html.Div(
                [
                    html.H1(
                        "Commodity Price Analytics",
                        style={
                            "color": COLORS["text"],
                            "margin": "0",
                            "fontSize": "28px",
                        },
                    ),
                    html.P(
                        "U.S. energy spot prices — trend, volatility, and forecast",
                        style={"color": COLORS["muted"], "margin": "4px 0 0 0"},
                    ),
                ],
                style={"marginBottom": "22px"},
            ),
            # --- Controls -------------------------------------------------
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Commodity", style=_LABEL_STYLE),
                            dcc.Dropdown(
                                id="commodity-dropdown",
                                options=options,
                                value=default_key,
                                clearable=False,
                                style={"width": "280px", "color": "#111"},
                            ),
                        ]
                    ),
                    html.Div(
                        [
                            html.Div("Date range", style=_LABEL_STYLE),
                            dcc.DatePickerRange(
                                id="date-range",
                                min_date_allowed=date_min,
                                max_date_allowed=date_max,
                                start_date=date_min,
                                end_date=date_max,
                                display_format="YYYY-MM-DD",
                            ),
                        ]
                    ),
                ],
                style={
                    "display": "flex",
                    "gap": "28px",
                    "alignItems": "flex-start",
                    "marginBottom": "22px",
                },
            ),
            # --- KPI cards ------------------------------------------------
            html.Div(
                [
                    _kpi_card("Current Price", "kpi-current"),
                    _kpi_card("Week-over-Week", "kpi-wow"),
                    _kpi_card("4-Week Average", "kpi-avg"),
                ],
                style={"display": "flex", "gap": "18px", "marginBottom": "22px"},
            ),
            # --- Charts ---------------------------------------------------
            dcc.Loading(
                color=COLORS["accent"],
                children=[
                    html.Div(
                        dcc.Graph(id="price-chart", config=_GRAPH_CONFIG),
                        style={
                            "backgroundColor": COLORS["panel"],
                            "borderRadius": "12px",
                            "padding": "10px",
                            "marginBottom": "18px",
                            "border": f"1px solid {COLORS['border']}",
                        },
                    ),
                    html.Div(
                        dcc.Graph(id="zscore-chart", config=_GRAPH_CONFIG),
                        style={
                            "backgroundColor": COLORS["panel"],
                            "borderRadius": "12px",
                            "padding": "10px",
                            "border": f"1px solid {COLORS['border']}",
                        },
                    ),
                ],
            ),
            html.P(
                "Data: U.S. Energy Information Administration (EIA). "
                "Forecast: ARIMA(1,1,1) with 95% confidence interval.",
                style={
                    "color": COLORS["muted"],
                    "fontSize": "12px",
                    "marginTop": "18px",
                },
            ),
        ],
    )
