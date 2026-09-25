#!/usr/bin/env python3
"""Generate an interactive HTML usage report from a Cursor team usage export CSV.

Times are normalized to US/Eastern. Product channels are inferred from Model:
  - Grok Bot          → model == grok-bot-default
  - Cursor · Grok     → other models with "grok" in the name
  - Cursor · other    → everything else
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots

TZ = "America/New_York"
TZ_LABEL = "ET"

COLORS = {
    "navy": "#0B3A5B",
    "teal": "#1A8A8C",
    "sky": "#4BA3C3",
    "slate": "#4A5568",
    "sand": "#E8EEF2",
    "ink": "#1A2332",
    "muted": "#64748B",
    "accent": "#C45C26",
    "green": "#2F9E6B",
    "amber": "#D4A017",
}

USER_COLORS = ["#0B3A5B", "#1A8A8C", "#C45C26", "#4BA3C3", "#2F9E6B", "#7C5CBF"]

CHANNEL_COLORS = {
    "Grok Bot": "#C45C26",
    "Cursor · Grok models": "#1A8A8C",
    "Cursor · other models": "#0B3A5B",
}

FAMILY_COLORS = {
    "Grok Bot": "#C45C26",
    "Cursor Grok": "#1A8A8C",
    "Auto": "#0B3A5B",
    "Composer": "#D4A017",
    "GPT": "#4BA3C3",
    "Claude": "#7C5CBF",
    "Other": "#64748B",
}

CHANNEL_ORDER = ["Grok Bot", "Cursor · Grok models", "Cursor · other models"]

# Account billing assumptions (Team seats are not in the usage export).
DEFAULT_SEAT_COST_USD = 40.0
DEFAULT_SEAT_EMAILS = (
    "erika.sacco@apexnc.org",
    "conrad.sain@apexnc.org",
    "fernando.guzman@apexnc.org",
    "connor.mckinnis@apexnc.org",
    "itsec@apexnc.org",
)

CHART_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="IBM Plex Sans, Segoe UI, system-ui, sans-serif", color="#1A2332", size=13),
    margin=dict(l=52, r=28, t=56, b=52),
    hoverlabel=dict(bgcolor="white", font_size=12, font_family="IBM Plex Sans, Segoe UI, sans-serif"),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
)


def short_email(email: str) -> str:
    return str(email).split("@")[0]


def fmt_day(ts: pd.Timestamp) -> str:
    """Month + day without a leading zero (portable; %-d breaks on Windows)."""
    return f"{ts.strftime('%b')} {ts.day}"


def fmt_when(ts: pd.Timestamp) -> str:
    """'Sep 5, 3:28 PM' without %-d / %-I (portable)."""
    hour = ts.hour % 12 or 12
    return f"{ts.strftime('%b')} {ts.day}, {hour}:{ts.strftime('%M %p')}"


def fmt_day_year(ts: pd.Timestamp) -> str:
    return f"{ts.strftime('%b')} {ts.day}, {ts.year}"


def fmt_when_year(ts: pd.Timestamp) -> str:
    hour = ts.hour % 12 or 12
    return f"{ts.strftime('%b')} {ts.day}, {ts.year} {hour}:{ts.strftime('%M %p')}"


def classify_channel(model: str) -> str:
    m = (model or "").strip().lower()
    if m == "grok-bot-default":
        return "Grok Bot"
    if "grok" in m:
        return "Cursor · Grok models"
    return "Cursor · other models"


def model_family(model: str) -> str:
    m = (model or "").strip().lower()
    if m == "grok-bot-default":
        return "Grok Bot"
    if "grok" in m:
        return "Cursor Grok"
    if m == "auto" or "auto balanced" in m:
        return "Auto"
    if "composer" in m:
        return "Composer"
    if "gpt" in m or "sol" in m:
        return "GPT"
    if "claude" in m or "opus" in m or "sonnet" in m:
        return "Claude"
    return "Other"


def parse_cost(value: object) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    s = str(value).strip()
    if s in {"", "-", "Free", "free"}:
        return 0.0
    try:
        return float(s.replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


def load_usage(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df.columns = [c.strip() for c in df.columns]
    df["Date_utc"] = pd.to_datetime(df["Date"], utc=True)
    df["Date_et"] = df["Date_utc"].dt.tz_convert(TZ)
    df["day"] = df["Date_et"].dt.normalize()
    df["day_label"] = df["Date_et"].map(fmt_day)
    df["weekday"] = df["Date_et"].dt.day_name()
    df["hour"] = df["Date_et"].dt.hour
    df["when_et"] = df["Date_et"].map(fmt_when) + f" {TZ_LABEL}"
    df["user"] = df["User"].map(short_email)
    df["channel"] = df["Model"].map(classify_channel)
    df["family"] = df["Model"].map(model_family)
    df["is_grok_bot"] = df["channel"].eq("Grok Bot")
    df["is_cursor"] = ~df["is_grok_bot"]
    for col in [
        "Input (w/ Cache Write)",
        "Input (w/o Cache Write)",
        "Cache Read",
        "Output Tokens",
        "Total Tokens",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
    df["cost_usd"] = df["Cost"].map(parse_cost)
    df["tokens_m"] = df["Total Tokens"] / 1_000_000
    df["channel"] = pd.Categorical(df["channel"], categories=CHANNEL_ORDER, ordered=True)
    return df.sort_values("Date_et")


def fmt_int(n: float | int) -> str:
    return f"{int(n):,}"


def fmt_tokens(n: float | int) -> str:
    n = float(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{int(n)}"


def style_fig(fig: go.Figure, title: str, subtitle: str | None = None) -> go.Figure:
    full_title = title if not subtitle else f"{title}<br><sup style='color:#64748B'>{subtitle}</sup>"
    fig.update_layout(
        title=dict(text=full_title, x=0, xanchor="left", font=dict(size=16, color=COLORS["ink"])),
        **CHART_LAYOUT,
    )
    fig.update_xaxes(gridcolor="#E2E8F0", zeroline=False, linecolor="#CBD5E1", title_standoff=10)
    fig.update_yaxes(gridcolor="#E2E8F0", zeroline=False, linecolor="#CBD5E1", title_standoff=10)
    return fig


def empty_fig(message: str, title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False, font=dict(size=14, color=COLORS["muted"]))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return style_fig(fig, title)


def chart_daily_events(df: pd.DataFrame, color_col: str = "user", title: str = "Events per day") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    daily = df.groupby(["day", color_col], as_index=False, observed=True).size().rename(columns={"size": "events"})
    color_map = CHANNEL_COLORS if color_col == "channel" else None
    seq = USER_COLORS if color_col == "user" else None
    fig = px.bar(
        daily,
        x="day",
        y="events",
        color=color_col,
        barmode="stack",
        color_discrete_map=color_map,
        color_discrete_sequence=seq,
        custom_data=[color_col],
    )
    fig.update_traces(
        hovertemplate=(
            f"<b>%{{customdata[0]}}</b><br>"
            f"Date (%{{x|%b %-d}}) {TZ_LABEL}<br>"
            "Events: %{y:,}<extra></extra>"
        )
    )
    fig.update_layout(hovermode="x unified", yaxis_title="Events", xaxis_title=f"Date ({TZ_LABEL})")
    return style_fig(fig, title, f"Stacked · calendar days in {TZ_LABEL}")


def chart_daily_tokens(df: pd.DataFrame, color_col: str = "user", title: str = "Tokens per day") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    daily = df.groupby(["day", color_col], as_index=False, observed=True)["Total Tokens"].sum()
    daily["tokens_m"] = daily["Total Tokens"] / 1_000_000
    color_map = CHANNEL_COLORS if color_col == "channel" else None
    seq = USER_COLORS if color_col == "user" else None
    fig = px.bar(
        daily,
        x="day",
        y="tokens_m",
        color=color_col,
        barmode="stack",
        color_discrete_map=color_map,
        color_discrete_sequence=seq,
        custom_data=[color_col, "Total Tokens"],
    )
    fig.update_traces(
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            f"Date (%{{x|%b %-d}}) {TZ_LABEL}<br>"
            "Tokens: %{customdata[1]:,.0f} (%{y:.2f}M)<extra></extra>"
        )
    )
    fig.update_layout(hovermode="x unified", yaxis_title="Tokens (millions)", xaxis_title=f"Date ({TZ_LABEL})")
    return style_fig(fig, title, f"Stacked · {TZ_LABEL} days")


def chart_user_summary(df: pd.DataFrame, title: str = "Usage by person") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    summary = (
        df.groupby("user", as_index=False)
        .agg(events=("user", "size"), tokens=("Total Tokens", "sum"), spend=("cost_usd", "sum"))
        .sort_values("events", ascending=True)
    )
    fig = make_subplots(rows=1, cols=2, shared_yaxes=True, subplot_titles=("Events", "Tokens (M)"), horizontal_spacing=0.12)
    fig.add_trace(
        go.Bar(
            x=summary["events"],
            y=summary["user"],
            orientation="h",
            marker_color=COLORS["navy"],
            name="Events",
            text=summary["events"].map(fmt_int),
            textposition="outside",
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>Events: %{x:,}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=summary["tokens"] / 1_000_000,
            y=summary["user"],
            orientation="h",
            marker_color=COLORS["teal"],
            name="Tokens (M)",
            text=(summary["tokens"] / 1_000_000).map(lambda v: f"{v:.1f}"),
            textposition="outside",
            cliponaxis=False,
            customdata=summary["tokens"],
            hovertemplate="<b>%{y}</b><br>Tokens: %{customdata:,.0f}<extra></extra>",
        ),
        row=1,
        col=2,
    )
    fig.update_layout(showlegend=False, height=max(280, 70 + 36 * len(summary)))
    return style_fig(fig, title)


def chart_channel_mix(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return empty_fig("No events", "Product channel mix")
    mix = (
        df.groupby("channel", as_index=False, observed=True)
        .agg(events=("channel", "size"), tokens=("Total Tokens", "sum"))
        .sort_values("events", ascending=False)
    )
    colors = [CHANNEL_COLORS.get(str(c), COLORS["muted"]) for c in mix["channel"]]
    fig = go.Figure(
        data=[
            go.Pie(
                labels=mix["channel"].astype(str),
                values=mix["events"],
                hole=0.58,
                marker=dict(colors=colors),
                textinfo="label+percent",
                textposition="outside",
                customdata=mix["tokens"],
                hovertemplate=(
                    "<b>%{label}</b><br>"
                    "Events: %{value:,} (%{percent})<br>"
                    "Tokens: %{customdata:,.0f}<extra></extra>"
                ),
            )
        ]
    )
    fig.update_layout(
        annotations=[dict(text="Channel", x=0.5, y=0.5, font_size=13, showarrow=False, font_color=COLORS["muted"])],
        showlegend=False,
        height=380,
        margin=dict(l=20, r=20, t=56, b=20),
    )
    return style_fig(
        fig,
        "Product channel mix",
        "Grok Bot = model grok-bot-default · Cursor Grok = IDE Grok models",
    )


def chart_family_mix(df: pd.DataFrame, title: str = "Model family mix") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    fam = (
        df.groupby("family", as_index=False)
        .agg(events=("family", "size"), tokens=("Total Tokens", "sum"))
        .sort_values("events", ascending=False)
    )
    colors = [FAMILY_COLORS.get(f, COLORS["muted"]) for f in fam["family"]]
    fig = go.Figure(
        data=[
            go.Pie(
                labels=fam["family"],
                values=fam["events"],
                hole=0.55,
                marker=dict(colors=colors),
                textinfo="label+percent",
                customdata=fam["tokens"],
                hovertemplate="<b>%{label}</b><br>Events: %{value:,}<br>Tokens: %{customdata:,.0f}<extra></extra>",
            )
        ]
    )
    fig.update_layout(showlegend=False, height=380, margin=dict(l=20, r=20, t=56, b=20))
    return style_fig(fig, title)


def chart_top_models(df: pd.DataFrame, top_n: int = 12, title: str = "Top models by events") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    models = (
        df.groupby(["Model", "channel"], as_index=False, observed=True)
        .agg(events=("Model", "size"), tokens=("Total Tokens", "sum"))
        .sort_values("events", ascending=False)
        .head(top_n)
        .sort_values("events", ascending=True)
    )
    fig = go.Figure(
        go.Bar(
            x=models["events"],
            y=models["Model"],
            orientation="h",
            marker_color=[CHANNEL_COLORS.get(str(c), COLORS["navy"]) for c in models["channel"]],
            customdata=models.assign(channel=models["channel"].astype(str))[["channel", "tokens"]].to_numpy(),
            text=models["events"].map(fmt_int),
            textposition="outside",
            cliponaxis=False,
            hovertemplate=(
                "<b>%{y}</b><br>"
                "Channel: %{customdata[0]}<br>"
                "Events: %{x:,}<br>"
                "Tokens: %{customdata[1]:,.0f}<extra></extra>"
            ),
        )
    )
    fig.update_layout(height=max(320, 48 + 28 * len(models)), xaxis_title="Events", yaxis_title="")
    return style_fig(fig, title, "Bar color = product channel")


def chart_kind(df: pd.DataFrame, title: str = "Billing kind") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    kind = df.groupby("Kind", as_index=False).size().rename(columns={"size": "events"})
    fig = px.bar(
        kind,
        x="Kind",
        y="events",
        color="Kind",
        color_discrete_sequence=[COLORS["teal"], COLORS["accent"], COLORS["sky"]],
        text="events",
    )
    fig.update_traces(texttemplate="%{text:,}", textposition="outside", cliponaxis=False, hovertemplate="<b>%{x}</b><br>Events: %{y:,}<extra></extra>")
    fig.update_layout(showlegend=False, yaxis_title="Events", xaxis_title="")
    return style_fig(fig, title)


def chart_heatmap(df: pd.DataFrame, title: str = "Activity heatmap") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    heat = df.groupby(["user", "day"], as_index=False).size().rename(columns={"size": "events"})
    pivot = heat.pivot(index="user", columns="day", values="events").fillna(0)
    x_labels = [fmt_day(pd.Timestamp(c)) for c in pivot.columns]
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=x_labels,
            y=list(pivot.index),
            colorscale=[[0, "#F8FAFC"], [0.35, "#A8D5D6"], [0.7, "#1A8A8C"], [1, "#0B3A5B"]],
            hovertemplate=f"User: %{{y}}<br>Day ({TZ_LABEL}): %{{x}}<br>Events: %{{z:.0f}}<extra></extra>",
            colorbar=dict(title="Events"),
        )
    )
    fig.update_layout(height=max(260, 80 + 40 * len(pivot.index)), xaxis_title=f"Date ({TZ_LABEL})", yaxis_title="")
    return style_fig(fig, title, f"Each cell = events that calendar day in {TZ_LABEL}")


def chart_hour_of_day(df: pd.DataFrame, title: str = "Hour of day") -> go.Figure:
    if df.empty:
        return empty_fig("No events in this view", title)
    hourly = df.groupby("hour", as_index=False).size().rename(columns={"size": "events"})
    fig = px.bar(hourly, x="hour", y="events", color_discrete_sequence=[COLORS["navy"]])
    fig.update_traces(hovertemplate=f"Hour ({TZ_LABEL}): %{{x}}:00<br>Events: %{{y:,}}<extra></extra>")
    fig.update_layout(
        xaxis=dict(dtick=1, title=f"Hour ({TZ_LABEL})", range=[-0.5, 23.5]),
        yaxis_title="Events",
        showlegend=False,
    )
    return style_fig(fig, title, f"Local Eastern Time ({TZ_LABEL})")


def chart_channel_compare_users(df: pd.DataFrame) -> go.Figure:
    if df.empty:
        return empty_fig("No events", "Channel by person")
    summary = (
        df.groupby(["user", "channel"], as_index=False, observed=True)
        .size()
        .rename(columns={"size": "events"})
    )
    fig = px.bar(
        summary,
        x="user",
        y="events",
        color="channel",
        barmode="group",
        color_discrete_map=CHANNEL_COLORS,
        category_orders={"channel": CHANNEL_ORDER},
    )
    fig.update_traces(hovertemplate="<b>%{fullData.name}</b><br>User: %{x}<br>Events: %{y:,}<extra></extra>")
    fig.update_layout(yaxis_title="Events", xaxis_title="", legend_title_text="")
    return style_fig(fig, "Who used which channel", "Grouped bars · same day range")


def account_spend(
    df: pd.DataFrame,
    seat_emails: tuple[str, ...] = DEFAULT_SEAT_EMAILS,
    seat_cost_usd: float = DEFAULT_SEAT_COST_USD,
) -> dict[str, float | int]:
    seats = len(seat_emails)
    license_usd = seats * seat_cost_usd
    on_demand_usd = float(df["cost_usd"].sum())
    total_usd = license_usd + on_demand_usd
    return {
        "seats": seats,
        "seat_cost_usd": seat_cost_usd,
        "license_usd": license_usd,
        "on_demand_usd": on_demand_usd,
        "total_usd": total_usd,
        "annual_usd": total_usd * 12,
    }


def chart_spend_composition(
    license_usd: float,
    on_demand_usd: float,
    title: str = "Account spend mix",
) -> go.Figure:
    labels = ["Team licenses", "On-demand usage"]
    values = [license_usd, on_demand_usd]
    colors = [COLORS["navy"], COLORS["accent"]]
    if sum(values) <= 0:
        return empty_fig("No spend to show", title)
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                hole=0.58,
                marker=dict(colors=colors),
                textinfo="label+percent",
                textposition="outside",
                hovertemplate="<b>%{label}</b><br>$%{value:,.2f} (%{percent})<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        annotations=[
            dict(
                text=f"${license_usd + on_demand_usd:,.0f}",
                x=0.5,
                y=0.5,
                font_size=16,
                showarrow=False,
                font_color=COLORS["navy"],
            )
        ],
        showlegend=False,
        height=380,
        margin=dict(l=20, r=20, t=56, b=20),
    )
    return style_fig(fig, title, "Licenses assumed flat · on-demand from export Cost")


def chart_spend_by_user(df: pd.DataFrame, title: str = "On-demand spend by person") -> go.Figure:
    summary = (
        df.groupby("user", as_index=False)
        .agg(spend=("cost_usd", "sum"), events=("user", "size"))
        .sort_values("spend", ascending=True)
    )
    if summary.empty or float(summary["spend"].sum()) <= 0:
        return empty_fig("No on-demand Cost in this export", title)
    fig = go.Figure(
        go.Bar(
            x=summary["spend"],
            y=summary["user"],
            orientation="h",
            marker_color=COLORS["accent"],
            text=summary["spend"].map(lambda v: f"${v:.2f}"),
            textposition="outside",
            cliponaxis=False,
            customdata=summary["events"],
            hovertemplate="<b>%{y}</b><br>On-demand: $%{x:.2f}<br>Events: %{customdata:,}<extra></extra>",
        )
    )
    fig.update_layout(height=max(280, 70 + 36 * len(summary)), xaxis_title="USD", yaxis_title="")
    return style_fig(fig, title, "Numeric Cost only · Included / Free rows count as $0")


def chart_spend(df: pd.DataFrame, title: str = "On-demand cost by model") -> go.Figure:
    spend = df[df["cost_usd"] > 0].copy()
    if spend.empty:
        return empty_fig("No numeric Cost rows in this view", title)
    by_model = (
        spend.groupby(["Model", "channel"], as_index=False, observed=True)["cost_usd"]
        .sum()
        .sort_values("cost_usd", ascending=True)
        .tail(10)
    )
    fig = go.Figure(
        go.Bar(
            x=by_model["cost_usd"],
            y=by_model["Model"],
            orientation="h",
            marker_color=[CHANNEL_COLORS.get(str(c), COLORS["accent"]) for c in by_model["channel"]],
            text=by_model["cost_usd"].map(lambda v: f"${v:.2f}"),
            textposition="outside",
            cliponaxis=False,
            customdata=by_model["channel"].astype(str),
            hovertemplate="<b>%{y}</b><br>Channel: %{customdata}<br>Billed: $%{x:.2f}<extra></extra>",
        )
    )
    fig.update_layout(showlegend=False, xaxis_title="USD", yaxis_title="")
    return style_fig(fig, title, "Only rows where Cost is a dollar amount")


def seat_roster_html(
    df: pd.DataFrame,
    seat_emails: tuple[str, ...] = DEFAULT_SEAT_EMAILS,
    seat_cost_usd: float = DEFAULT_SEAT_COST_USD,
) -> str:
    active = {str(u).lower() for u in df["User"].unique()}
    rows = []
    for email in seat_emails:
        short = short_email(email)
        in_export = email.lower() in active
        user_df = df[df["User"].str.lower() == email.lower()]
        events = len(user_df)
        on_demand = float(user_df["cost_usd"].sum()) if events else 0.0
        status = "Active in export" if in_export else "Seat only (no events)"
        rows.append(
            f"""
            <tr>
              <td>{short}</td>
              <td class="mono">{email}</td>
              <td>{status}</td>
              <td class="num">{fmt_int(events)}</td>
              <td class="num">${seat_cost_usd:.0f}</td>
              <td class="num">{"—" if on_demand == 0 else f"${on_demand:.2f}"}</td>
            </tr>
            """
        )
    return f"""
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th>Seat</th>
            <th>Email</th>
            <th>Status</th>
            <th>Events</th>
            <th>License / mo</th>
            <th>On-demand</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def recent_events_table(df: pd.DataFrame, limit: int = 40) -> str:
    if df.empty:
        return '<p class="empty">No events in this view.</p>'
    rows = []
    sample = df.sort_values("Date_et", ascending=False).head(limit)
    for _, r in sample.iterrows():
        rows.append(
            f"""
            <tr>
              <td>{r['when_et']}</td>
              <td>{r['user']}</td>
              <td><span class="chip chip-{slug(str(r['channel']))}">{r['channel']}</span></td>
              <td class="mono">{r['Model']}</td>
              <td>{r['Kind']}</td>
              <td class="num">{fmt_tokens(r['Total Tokens'])}</td>
              <td class="num">{"—" if r['cost_usd'] == 0 else f"${r['cost_usd']:.2f}"}</td>
            </tr>
            """
        )
    return f"""
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th>When ({TZ_LABEL})</th>
            <th>User</th>
            <th>Channel</th>
            <th>Model</th>
            <th>Kind</th>
            <th>Tokens</th>
            <th>Billed</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def user_table_html(df: pd.DataFrame) -> str:
    if df.empty:
        return '<p class="empty">No events in this view.</p>'
    summary = (
        df.groupby(["user", "User"], as_index=False)
        .agg(
            events=("user", "size"),
            tokens=("Total Tokens", "sum"),
            output=("Output Tokens", "sum"),
            spend=("cost_usd", "sum"),
            models=("Model", "nunique"),
            grok_bot=("is_grok_bot", "sum"),
            cursor_grok=("channel", lambda s: int((s == "Cursor · Grok models").sum())),
            first=("Date_et", "min"),
            last=("Date_et", "max"),
        )
        .sort_values("events", ascending=False)
    )
    rows = []
    for _, r in summary.iterrows():
        rows.append(
            f"""
            <tr>
              <td>{r['user']}</td>
              <td class="num">{fmt_int(r['events'])}</td>
              <td class="num">{fmt_tokens(r['tokens'])}</td>
              <td class="num">{fmt_int(r['cursor_grok'])}</td>
              <td class="num">{fmt_int(r['grok_bot'])}</td>
              <td class="num">{int(r['models'])}</td>
              <td class="num">${r['spend']:.2f}</td>
              <td>{fmt_when(r['first'])} {TZ_LABEL}</td>
              <td>{fmt_when(r['last'])} {TZ_LABEL}</td>
            </tr>
            """
        )
    return f"""
    <div class="table-wrap">
      <table class="data">
        <thead>
          <tr>
            <th>User</th>
            <th>Events</th>
            <th>Tokens</th>
            <th>Cursor Grok</th>
            <th>Grok Bot</th>
            <th>Models</th>
            <th>Billed $</th>
            <th>First ({TZ_LABEL})</th>
            <th>Last ({TZ_LABEL})</th>
          </tr>
        </thead>
        <tbody>{''.join(rows)}</tbody>
      </table>
    </div>
    """


def slug(text: str) -> str:
    return (
        text.lower()
        .replace("·", "")
        .replace(" ", "-")
        .replace("──", "-")
        .replace("--", "-")
    )


def kpi_card(label: str, value: str, hint: str = "") -> str:
    hint_html = f'<div class="kpi-hint">{hint}</div>' if hint else ""
    return f"""
    <div class="kpi">
      <div class="kpi-label">{label}</div>
      <div class="kpi-value">{value}</div>
      {hint_html}
    </div>
    """


def fig_html(fig: go.Figure, include_js: bool = False) -> str:
    # Embed Plotly JS (not CDN) so the report works on air-gapped / firewall hosts.
    return fig.to_html(
        full_html=False,
        include_plotlyjs=True if include_js else False,
        config={"displayModeBar": True, "responsive": True, "displaylogo": False},
    )


def section(title: str, body: str, blurb: str = "") -> str:
    blurb_html = f"<p>{blurb}</p>" if blurb else ""
    return f"""
    <div class="section">
      <h2>{title}</h2>
      {blurb_html}
      {body}
    </div>
    """


def grid(cards: list[str], klass: str = "") -> str:
    return f'<div class="grid {klass}">' + "".join(f'<div class="card chart">{c}</div>' if not c.startswith("<div") else c for c in cards) + "</div>"


def chart_card(fig: go.Figure, include_js: bool = False) -> str:
    return f'<div class="card chart">{fig_html(fig, include_js=include_js)}</div>'


def build_overview(
    df: pd.DataFrame,
    first_js: list[bool],
    seat_emails: tuple[str, ...] = DEFAULT_SEAT_EMAILS,
    seat_cost_usd: float = DEFAULT_SEAT_COST_USD,
) -> str:
    include = first_js[0]
    first_js[0] = False
    start = fmt_day_year(df["Date_et"].min())
    end = fmt_day_year(df["Date_et"].max())
    total_events = len(df)
    total_tokens = int(df["Total Tokens"].sum())
    users = df["user"].nunique()
    bot = int(df["is_grok_bot"].sum())
    cursor_grok = int((df["channel"] == "Cursor · Grok models").sum())
    cursor_other = int((df["channel"] == "Cursor · other models").sum())
    spend = account_spend(df, seat_emails=seat_emails, seat_cost_usd=seat_cost_usd)
    included_n = int((df["Kind"] == "Included").sum()) if "Kind" in df.columns else 0
    on_demand_n = int((df["Kind"] == "On-Demand").sum()) if "Kind" in df.columns else 0

    spend_kpis = f"""
    <div class="kpis spend-kpis">
      {kpi_card("Est. month total", f"${spend['total_usd']:,.2f}", f"{int(spend['seats'])} seats × ${seat_cost_usd:.0f} + on-demand")}
      {kpi_card("Team licenses", f"${spend['license_usd']:,.2f}", f"{int(spend['seats'])} × ${seat_cost_usd:.0f}/mo")}
      {kpi_card("On-demand usage", f"${spend['on_demand_usd']:,.2f}", "Numeric Cost in export")}
      {kpi_card("Annualized run-rate", f"${spend['annual_usd']:,.0f}", "×12 from this month’s estimate")}
    </div>
    """

    volume_kpis = f"""
    <div class="kpis">
      {kpi_card("Events", fmt_int(total_events), f"{users} people active in export · {start} – {end} {TZ_LABEL}")}
      {kpi_card("Total tokens", fmt_tokens(total_tokens), "input + cache + output")}
      {kpi_card("Included rows", fmt_int(included_n), "covered by seats (Cost blank/Free)")}
      {kpi_card("On-demand rows", fmt_int(on_demand_n), "extra billed usage")}
      {kpi_card("Grok Bot", fmt_int(bot), "model = grok-bot-default")}
      {kpi_card("Cursor · Grok", fmt_int(cursor_grok), "IDE Grok models")}
      {kpi_card("Cursor · other", fmt_int(cursor_other), "Auto, Composer, GPT, …")}
    </div>
    """

    return (
        '<div class="callout spend-callout">'
        "<b>Account spend first.</b> Team licenses are not in the usage export — "
        f"this report assumes <b>{int(spend['seats'])} seats at ${seat_cost_usd:.0f}/mo</b>, "
        "then adds on-demand Cost from the CSV. Most volume is Included / Free and shows as $0 here."
        "</div>"
        + spend_kpis
        + section(
            "Where the money goes",
            chart_card(chart_spend_composition(float(spend["license_usd"]), float(spend["on_demand_usd"])), include_js=include)
            + chart_card(chart_spend_by_user(df))
            + chart_card(chart_spend(df, title="On-demand cost by model"))
            + f'<div class="card">{seat_roster_html(df, seat_emails=seat_emails, seat_cost_usd=seat_cost_usd)}</div>',
            "Licenses are flat per seat. On-demand is only the dollar amounts Cursor put in the Cost column.",
        )
        + section(
            "Usage volume (detail)",
            volume_kpis,
            "Secondary metrics for exploration — not the primary cost story.",
        )
        + section(
            "How usage splits",
            chart_card(chart_channel_mix(df))
            + chart_card(chart_channel_compare_users(df))
            + '<div class="legend-note">'
            + "<strong>How to read channels:</strong> "
            + "<span class='swatch bot'></span> <b>Grok Bot</b> = coworker bot sessions (model <code>grok-bot-default</code>). "
            + "<span class='swatch cursor-grok'></span> <b>Cursor · Grok</b> = using Grok models inside Cursor IDE. "
            + "<span class='swatch cursor-other'></span> <b>Cursor · other</b> = Cursor with Auto, Composer, GPT, Claude, etc."
            + "</div>",
            "Export does not label product separately — we infer channel from the Model field.",
        )
        + section(
            "Volume over the month",
            chart_card(chart_daily_events(df, color_col="channel", title="Events per day by channel"))
            + chart_card(chart_daily_tokens(df, color_col="channel", title="Tokens per day by channel")),
            f"All dates and hours are converted from UTC → {TZ_LABEL}.",
        )
        + section(
            "People & models",
            chart_card(chart_user_summary(df))
            + chart_card(chart_top_models(df, title="Top models (color = channel)")),
        )
        + section(
            "Patterns",
            chart_card(chart_heatmap(df))
            + '<div class="grid pair">'
            + chart_card(chart_hour_of_day(df))
            + chart_card(chart_kind(df))
            + "</div>",
        )
        + section(
            "Per-user rollup",
            f'<div class="card">{user_table_html(df)}</div>',
            "Cursor Grok vs Grok Bot counted separately.",
        )
        + section(
            "Recent events",
            f'<div class="card">{recent_events_table(df)}</div>',
            f"Newest 40 rows · timestamps shown in {TZ_LABEL}.",
        )
    )


def build_cursor_page(df: pd.DataFrame, first_js: list[bool]) -> str:
    cur = df[df["is_cursor"]].copy()
    include = first_js[0]
    first_js[0] = False
    if cur.empty:
        return '<p class="empty">No Cursor IDE events in this export.</p>'

    grok_n = int((cur["channel"] == "Cursor · Grok models").sum())
    other_n = int((cur["channel"] == "Cursor · other models").sum())
    kpis = f"""
    <div class="kpis">
      {kpi_card("Cursor events", fmt_int(len(cur)), "excludes Grok Bot")}
      {kpi_card("Tokens", fmt_tokens(cur["Total Tokens"].sum()), "")}
      {kpi_card("Using Grok models", fmt_int(grok_n), "still Cursor IDE")}
      {kpi_card("Other models", fmt_int(other_n), "Auto, Composer, GPT, …")}
      {kpi_card("People", str(cur["user"].nunique()), "")}
    </div>
    """
    return (
        '<div class="callout">'
        "<b>Cursor IDE only.</b> This page drops <code>grok-bot-default</code>. "
        "“Cursor · Grok models” means someone picked a Grok model inside Cursor — not the Grok Bot coworker."
        "</div>"
        + kpis
        + section(
            "Daily Cursor activity",
            chart_card(chart_daily_events(cur, color_col="user", title="Cursor events per day"), include_js=include)
            + chart_card(chart_daily_tokens(cur, color_col="user", title="Cursor tokens per day")),
        )
        + section(
            "Grok-in-Cursor vs other Cursor models",
            chart_card(chart_daily_events(cur, color_col="channel", title="Cursor events by model channel"))
            + chart_card(chart_family_mix(cur, title="Cursor model family mix")),
        )
        + section(
            "Who & what",
            chart_card(chart_user_summary(cur, title="Cursor usage by person"))
            + chart_card(chart_top_models(cur, title="Top Cursor models")),
        )
        + section(
            "Patterns",
            chart_card(chart_heatmap(cur, title="Cursor activity heatmap"))
            + chart_card(chart_hour_of_day(cur, title="Cursor hour of day")),
        )
        + section("Spend (Cursor rows with numeric Cost)", chart_card(chart_spend(cur)))
        + section("Recent Cursor events", f'<div class="card">{recent_events_table(cur)}</div>')
    )


def build_grok_bot_page(df: pd.DataFrame, first_js: list[bool]) -> str:
    bot = df[df["is_grok_bot"]].copy()
    include = first_js[0]
    first_js[0] = False
    if bot.empty:
        return (
            '<div class="callout">'
            "<b>No Grok Bot events</b> in this export "
            "(nothing with model <code>grok-bot-default</code>)."
            "</div>"
        )

    kpis = f"""
    <div class="kpis">
      {kpi_card("Grok Bot events", fmt_int(len(bot)), "model = grok-bot-default")}
      {kpi_card("Tokens", fmt_tokens(bot["Total Tokens"].sum()), "")}
      {kpi_card("People", str(bot["user"].nunique()), ", ".join(sorted(bot["user"].unique())))}
      {kpi_card("Active days", str(bot["day"].nunique()), f"{TZ_LABEL} calendar days")}
      {kpi_card("Share of all events", f"{100 * len(bot) / len(df):.0f}%", "of full export")}
    </div>
    """
    return (
        '<div class="callout bot">'
        "<b>Grok Bot only.</b> Filtered to model <code>grok-bot-default</code>. "
        "This is the coworker bot — not picking Grok models inside the Cursor IDE."
        "</div>"
        + kpis
        + section(
            "Grok Bot daily volume",
            chart_card(chart_daily_events(bot, color_col="user", title="Grok Bot events per day"), include_js=include)
            + chart_card(chart_daily_tokens(bot, color_col="user", title="Grok Bot tokens per day")),
        )
        + section(
            "Who used Grok Bot",
            chart_card(chart_user_summary(bot, title="Grok Bot by person"))
            + chart_card(chart_hour_of_day(bot, title="Grok Bot hour of day")),
        )
        + section(
            "Patterns",
            chart_card(chart_heatmap(bot, title="Grok Bot heatmap"))
            + chart_card(chart_kind(bot, title="Grok Bot billing kind")),
        )
        + section("Recent Grok Bot events", f'<div class="card">{recent_events_table(bot, limit=50)}</div>')
    )


def build_compare_page(df: pd.DataFrame, first_js: list[bool]) -> str:
    include = first_js[0]
    first_js[0] = False
    return (
        '<div class="callout">'
        "<b>Side-by-side.</b> Same metrics for Grok Bot vs Cursor · Grok vs Cursor · other."
        "</div>"
        + section(
            "Channel comparison",
            chart_card(chart_channel_compare_users(df), include_js=include)
            + chart_card(chart_daily_events(df, color_col="channel", title="Daily events by channel"))
            + chart_card(chart_daily_tokens(df, color_col="channel", title="Daily tokens by channel"))
            + chart_card(chart_top_models(df, top_n=15, title="Top models across channels")),
        )
    )


def build_report(
    df: pd.DataFrame,
    source_name: str,
    seat_emails: tuple[str, ...] = DEFAULT_SEAT_EMAILS,
    seat_cost_usd: float = DEFAULT_SEAT_COST_USD,
) -> str:
    start = df["Date_et"].min()
    end = df["Date_et"].max()
    first_js = [True]
    spend = account_spend(df, seat_emails=seat_emails, seat_cost_usd=seat_cost_usd)

    overview = build_overview(df, first_js, seat_emails=seat_emails, seat_cost_usd=seat_cost_usd)
    cursor_page = build_cursor_page(df, first_js)
    bot_page = build_grok_bot_page(df, first_js)
    compare_page = build_compare_page(df, first_js)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Cursor / Grok Bot usage — {start.strftime('%b %Y')}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@500&display=swap" rel="stylesheet" />
  <style>
    :root {{
      --ink: {COLORS["ink"]};
      --muted: {COLORS["muted"]};
      --navy: {COLORS["navy"]};
      --teal: {COLORS["teal"]};
      --accent: {COLORS["accent"]};
      --sand: {COLORS["sand"]};
      --card: #ffffff;
      --line: #D8E0E8;
      --bg: linear-gradient(165deg, #F3F7FA 0%, #E7EEF4 45%, #F7F4EF 100%);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "IBM Plex Sans", Segoe UI, system-ui, sans-serif;
      color: var(--ink);
      background: var(--bg);
      min-height: 100vh;
    }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 36px 24px 64px; }}
    header {{ margin-bottom: 20px; padding-bottom: 18px; border-bottom: 1px solid var(--line); }}
    .eyebrow {{
      text-transform: uppercase; letter-spacing: 0.12em; font-size: 12px;
      font-weight: 600; color: var(--teal); margin-bottom: 8px;
    }}
    h1 {{
      margin: 0 0 8px; font-size: clamp(1.55rem, 2.3vw, 2rem);
      font-weight: 700; letter-spacing: -0.02em; color: var(--navy);
    }}
    .sub {{ margin: 0; color: var(--muted); max-width: 68ch; line-height: 1.5; }}
    .meta {{
      margin-top: 12px; font-family: "IBM Plex Mono", ui-monospace, monospace;
      font-size: 12px; color: var(--muted);
    }}
    .tabs {{
      display: flex; flex-wrap: wrap; gap: 8px; margin: 20px 0 8px;
      position: sticky; top: 0; z-index: 20;
      padding: 10px 0; background: rgba(243, 247, 250, 0.92);
      backdrop-filter: blur(8px); border-bottom: 1px solid transparent;
    }}
    .tab {{
      border: 1px solid var(--line); background: white; color: var(--ink);
      border-radius: 999px; padding: 8px 14px; font: inherit; font-size: 0.92rem;
      font-weight: 600; cursor: pointer;
    }}
    .tab:hover {{ border-color: #9BB4C7; }}
    .tab.active {{ background: var(--navy); color: white; border-color: var(--navy); }}
    .tab .count {{
      display: inline-block; margin-left: 6px; padding: 1px 7px; border-radius: 999px;
      font-size: 11px; font-weight: 600; background: rgba(255,255,255,0.18); color: inherit;
    }}
    .tab:not(.active) .count {{ background: var(--sand); color: var(--muted); }}
    .panel {{ display: none; }}
    .panel.active {{ display: block; }}
    .kpis {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px; margin: 18px 0 22px;
    }}
    .spend-kpis .kpi:first-child {{
      border-color: #9BB4C7;
      background: linear-gradient(160deg, #ffffff 0%, #EEF5F9 100%);
    }}
    .spend-kpis .kpi:first-child .kpi-value {{ font-size: 1.65rem; }}
    .kpi {{
      background: var(--card); border: 1px solid var(--line); border-radius: 12px;
      padding: 14px 16px; box-shadow: 0 1px 0 rgba(11, 58, 91, 0.04);
    }}
    .spend-callout {{ border-left: 4px solid {COLORS["navy"]}; }}
    .kpi-label {{
      font-size: 11px; font-weight: 600; color: var(--muted);
      text-transform: uppercase; letter-spacing: 0.06em;
    }}
    .kpi-value {{
      margin-top: 6px; font-size: 1.4rem; font-weight: 700;
      color: var(--navy); letter-spacing: -0.02em;
    }}
    .kpi-hint {{ margin-top: 4px; font-size: 12px; color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 16px; }}
    @media (min-width: 960px) {{
      .grid.pair {{ grid-template-columns: 1fr 1fr; }}
    }}
    .card {{
      background: var(--card); border: 1px solid var(--line); border-radius: 14px;
      padding: 8px 8px 4px; box-shadow: 0 8px 24px rgba(11, 58, 91, 0.05);
      margin-bottom: 16px;
    }}
    .section {{ margin: 26px 0 8px; }}
    .section h2 {{ margin: 0 0 6px; font-size: 1.12rem; color: var(--navy); }}
    .section > p {{ margin: 0 0 14px; color: var(--muted); font-size: 0.95rem; }}
    .callout {{
      background: #EEF6F7; border: 1px solid #C5DEE0; border-radius: 12px;
      padding: 12px 14px; color: var(--ink); margin: 8px 0 18px; line-height: 1.45;
    }}
    .callout.bot {{ background: #FBF3EC; border-color: #E8CDB8; }}
    .legend-note {{
      background: white; border: 1px solid var(--line); border-radius: 12px;
      padding: 12px 14px; margin: 0 0 8px; color: var(--muted); line-height: 1.5; font-size: 0.92rem;
    }}
    .swatch {{
      display: inline-block; width: 10px; height: 10px; border-radius: 2px;
      margin: 0 4px 0 8px; vertical-align: middle;
    }}
    .swatch.bot {{ background: {COLORS["accent"]}; }}
    .swatch.cursor-grok {{ background: {COLORS["teal"]}; }}
    .swatch.cursor-other {{ background: {COLORS["navy"]}; }}
    .table-wrap {{ overflow-x: auto; padding: 4px 8px 12px; }}
    table.data {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
    table.data th, table.data td {{
      padding: 9px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top;
    }}
    table.data th {{
      font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
      color: var(--muted); font-weight: 600; white-space: nowrap;
    }}
    table.data td.num, table.data td.mono {{
      font-variant-numeric: tabular-nums;
      font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 0.82rem;
    }}
    table.data tbody tr:hover {{ background: #F7FAFC; }}
    .chip {{
      display: inline-block; padding: 2px 8px; border-radius: 999px;
      font-size: 11px; font-weight: 600; white-space: nowrap;
    }}
    .chip-grok-bot {{ background: #F8E6D8; color: #8A3D12; }}
    .chip-cursor-grok-models {{ background: #D8EEEE; color: #0F5E60; }}
    .chip-cursor-other-models {{ background: #D9E4EE; color: #0B3A5B; }}
    .empty {{ color: var(--muted); padding: 12px; }}
    footer {{
      margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--line);
      color: var(--muted); font-size: 12px;
    }}
    code {{ font-family: "IBM Plex Mono", ui-monospace, monospace; font-size: 0.86em; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="eyebrow">Town of Apex · Innovations</div>
      <h1>Cursor / Grok Bot usage</h1>
      <p class="sub">
        Account-level spend first (Team licenses + on-demand), then usage detail.
        All timestamps converted to Eastern Time ({TZ_LABEL}).
        Tabs separate <b>Cursor IDE</b> from <b>Grok Bot</b> (<code>grok-bot-default</code>).
      </p>
      <div class="meta">{source_name} · {fmt_when_year(start)} → {fmt_when_year(end)} {TZ_LABEL} · {fmt_int(len(df))} events · est. ${spend['total_usd']:,.2f}/mo ({int(spend['seats'])}×${seat_cost_usd:.0f} + ${spend['on_demand_usd']:,.2f} on-demand)</div>
    </header>

    <nav class="tabs" role="tablist" aria-label="Report views">
      <button class="tab active" data-tab="overview" type="button">Overview</button>
      <button class="tab" data-tab="cursor" type="button">Cursor IDE <span class="count">{fmt_int(int((~df['is_grok_bot']).sum()))}</span></button>
      <button class="tab" data-tab="grokbot" type="button">Grok Bot <span class="count">{fmt_int(int(df['is_grok_bot'].sum()))}</span></button>
      <button class="tab" data-tab="compare" type="button">Compare</button>
    </nav>

    <div id="overview" class="panel active" role="tabpanel">{overview}</div>
    <div id="cursor" class="panel" role="tabpanel">{cursor_page}</div>
    <div id="grokbot" class="panel" role="tabpanel">{bot_page}</div>
    <div id="compare" class="panel" role="tabpanel">{compare_page}</div>

    <footer>
      Channel inference: <code>grok-bot-default</code> → Grok Bot;
      other model names containing “grok” → Cursor · Grok models;
      everything else → Cursor · other models.
      Team licenses assumed at ${seat_cost_usd:.0f}/seat/mo × {int(spend['seats'])} seats (not in the CSV).
      On-demand Cost is incomplete for Included / Free rows. Times: UTC → America/New_York.
    </footer>
  </div>
  <script>
    const tabs = document.querySelectorAll('.tab');
    const panels = document.querySelectorAll('.panel');
    function activate(id) {{
      tabs.forEach(t => t.classList.toggle('active', t.dataset.tab === id));
      panels.forEach(p => p.classList.toggle('active', p.id === id));
      // Plotly needs a resize after becoming visible
      window.dispatchEvent(new Event('resize'));
      history.replaceState(null, '', '#' + id);
    }}
    tabs.forEach(tab => tab.addEventListener('click', () => activate(tab.dataset.tab)));
    const initial = (location.hash || '#overview').slice(1);
    if ([...panels].some(p => p.id === initial)) activate(initial);
  </script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv",
        nargs="?",
        default=None,
        help="Path to team-usage-events CSV (default: newest matching file in this folder)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output HTML path (default: report.html next to the CSV)",
    )
    parser.add_argument(
        "--seat-cost",
        type=float,
        default=DEFAULT_SEAT_COST_USD,
        help=f"Team seat price USD/mo (default: {DEFAULT_SEAT_COST_USD:.0f})",
    )
    parser.add_argument(
        "--seats",
        nargs="+",
        default=list(DEFAULT_SEAT_EMAILS),
        help="Seat emails for license math (default: current Apex Team roster)",
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    if args.csv:
        csv_path = Path(args.csv).expanduser().resolve()
    else:
        matches = sorted(here.glob("team-usage-events-*.csv"))
        if not matches:
            raise SystemExit(f"No team-usage-events-*.csv found in {here}")
        csv_path = matches[-1]

    out_path = Path(args.output).expanduser().resolve() if args.output else here / "report.html"
    seat_emails = tuple(args.seats)

    df = load_usage(csv_path)
    html = build_report(df, csv_path.name, seat_emails=seat_emails, seat_cost_usd=args.seat_cost)
    out_path.write_text(html, encoding="utf-8")
    spend = account_spend(df, seat_emails=seat_emails, seat_cost_usd=args.seat_cost)
    print(f"Wrote {out_path}")
    print(
        f"  est. month=${spend['total_usd']:,.2f}  "
        f"(licenses ${spend['license_usd']:,.2f} + on-demand ${spend['on_demand_usd']:,.2f})  "
        f"annualized≈${spend['annual_usd']:,.0f}"
    )
    counts = df["channel"].value_counts()
    print(f"  rows={len(df):,}  ET range={df['Date_et'].min()} → {df['Date_et'].max()}")
    for channel, n in counts.items():
        print(f"  {channel}: {n}")


if __name__ == "__main__":
    main()
