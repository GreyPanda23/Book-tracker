"""Altair charts for the dashboard, with a colour-blind-checked palette.

Each store keeps the same colour everywhere (colour follows the store, not its
rank). Light and dark mode use the same hues stepped for each background.
"""

from __future__ import annotations

import altair as alt
import pandas as pd

from .fetchers.registry import load_store_configs

# Reference categorical palette, validated (scripts/validate_palette.js) for
# both backgrounds: CVD separation, normal-vision floor, lightness, chroma.
SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
INK = {"light": ("#0b0b0b", "#52514e", "#e6e5e0"), "dark": ("#ffffff", "#c3c2b7", "#3a3a37")}


def store_colors(dark: bool = False) -> dict[str, str]:
    palette = SERIES_DARK if dark else SERIES_LIGHT
    names = [c.name for c in load_store_configs()]
    return {name: palette[i % len(palette)] for i, name in enumerate(names)}


def _axis_config(chart: alt.Chart, dark: bool) -> alt.Chart:
    text, muted, grid = INK["dark" if dark else "light"]
    return (chart.configure_view(strokeWidth=0)
            .configure_axis(labelColor=muted, titleColor=muted, gridColor=grid, domainColor=grid,
                            tickColor=grid, labelFontSize=12, titleFontSize=12)
            .configure_legend(labelColor=text, titleColor=muted, orient="top", labelFontSize=12)
            .configure(background="transparent"))


def price_history_chart(history: list[dict], dark: bool = False) -> alt.Chart | None:
    """One 2px line per store (online price), point markers, hover tooltips."""
    if not history:
        return None
    df = pd.DataFrame(history)
    # In-store rows repeat the website price, so chart the online row per store/date
    df = (df.sort_values("type").drop_duplicates(["store_name", "date_checked"], keep="last"))
    df["date"] = pd.to_datetime(df["date_checked"]).dt.tz_localize(None).dt.normalize()
    df["stock"] = df["in_stock"].map({1: "In stock", 0: "Out of stock"}).fillna("Unknown")
    colors = store_colors(dark)
    stores = [s for s in colors if s in set(df["store_name"])]
    color = alt.Color("store_name:N", title=None,
                      scale=alt.Scale(domain=stores, range=[colors[s] for s in stores]),
                      legend=alt.Legend(orient="top", direction="horizontal", columns=3,
                                        symbolType="circle", symbolSize=80))
    hover = alt.selection_point(fields=["date"], nearest=True, on="pointerover", empty=False)
    base = alt.Chart(df).encode(
        x=alt.X("date:T", title=None, axis=alt.Axis(format="%d %b", tickCount=6, grid=False)),
        y=alt.Y("price_aed:Q", title="AED", scale=alt.Scale(zero=False, nice=True)),
        color=color)
    lines = base.mark_line(strokeWidth=2, interpolate="monotone")
    points = base.mark_point(filled=True, size=70, opacity=1).encode(
        tooltip=[alt.Tooltip("store_name:N", title="Store"),
                 alt.Tooltip("date:T", title="Checked", format="%d %b %Y"),
                 alt.Tooltip("price_aed:Q", title="Price (AED)", format=",.2f"),
                 alt.Tooltip("stock:N", title="Stock")])
    rule = (alt.Chart(df).mark_rule(strokeWidth=1, color=INK["dark" if dark else "light"][1])
            .encode(x="date:T", opacity=alt.condition(hover, alt.value(0.6), alt.value(0)))
            .add_params(hover))
    return _axis_config(alt.layer(lines, points, rule).properties(height=280), dark)


def bar_chart(rows: list[tuple[str, int]], title_x: str, dark: bool = False,
              horizontal: bool = True) -> alt.Chart | None:
    """Single-series bar chart (one colour, no legend) with value labels."""
    if not rows or not any(v for _, v in rows):
        return None
    df = pd.DataFrame(rows, columns=["label", "value"])
    color = (SERIES_DARK if dark else SERIES_LIGHT)[0]
    text_color = INK["dark" if dark else "light"][0]
    order = list(df["label"])
    if horizontal:
        enc = dict(y=alt.Y("label:N", sort=order, title=None),
                   x=alt.X("value:Q", title=None, axis=None,
                           scale=alt.Scale(domainMax=float(df["value"].max()) * 1.15)))
        bars = alt.Chart(df).mark_bar(color=color, cornerRadiusEnd=4, height={"band": 0.7})
        labels = alt.Chart(df).mark_text(align="left", dx=6, color=text_color, fontSize=12).encode(
            text="value:Q", **enc)
    else:
        enc = dict(x=alt.X("label:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)),
                   y=alt.Y("value:Q", title=title_x, axis=alt.Axis(tickMinStep=1),
                           scale=alt.Scale(domainMax=float(df["value"].max()) * 1.25)))
        bars = alt.Chart(df).mark_bar(color=color, cornerRadiusEnd=4, width={"band": 0.7})
        labels = alt.Chart(df).transform_filter("datum.value > 0").mark_text(
            dy=-8, color=text_color, fontSize=12).encode(text="value:Q", **enc)
    bars = bars.encode(tooltip=[alt.Tooltip("label:N", title=" "), alt.Tooltip("value:Q", title=title_x)], **enc)
    height = max(140, 34 * len(df)) if horizontal else 220
    return _axis_config(alt.layer(bars, labels).properties(height=height), dark)
