"""Build a copy-paste-friendly text pack of every chart grouping for AI analysis."""

from __future__ import annotations

from io import StringIO

import pandas as pd

from generate_report import (
    DEFAULT_SEAT_COST_USD,
    DEFAULT_SEAT_EMAILS,
    CHANNEL_ORDER,
    TZ_LABEL,
    account_spend,
    fmt_day,
    fmt_day_year,
    fmt_int,
    fmt_tokens,
    fmt_when_year,
)


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_(empty)_\n"
    cols = list(df.columns)
    lines = [
        "| " + " | ".join(str(c) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            v = row[c]
            if isinstance(v, float):
                if abs(v - round(v)) < 1e-9:
                    cells.append(str(int(round(v))))
                else:
                    cells.append(f"{v:.4g}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _daily_channel_pivot(df: pd.DataFrame, value_col: str | None = None) -> pd.DataFrame:
    """Pivot day × channel. value_col=None → event counts; else sum that column."""
    if df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["day_label"] = work["day"].map(lambda d: fmt_day(pd.Timestamp(d)))
    if value_col is None:
        g = work.groupby(["day", "day_label", "channel"], observed=True).size().rename("value")
    else:
        g = work.groupby(["day", "day_label", "channel"], observed=True)[value_col].sum().rename("value")
    g = g.reset_index()
    pivot = g.pivot_table(
        index=["day", "day_label"],
        columns="channel",
        values="value",
        fill_value=0,
        observed=True,
    )
    for ch in CHANNEL_ORDER:
        if ch not in pivot.columns:
            pivot[ch] = 0
    pivot = pivot[[c for c in CHANNEL_ORDER if c in pivot.columns]]
    pivot = pivot.reset_index().sort_values("day")
    out = pivot.drop(columns=["day"]).rename(columns={"day_label": "Day"})
    out["Total"] = out[[c for c in CHANNEL_ORDER if c in out.columns]].sum(axis=1)
    return out


def _heatmap_table(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    heat = df.groupby(["user", "day"], as_index=False).size().rename(columns={"size": "events"})
    pivot = heat.pivot(index="user", columns="day", values="events").fillna(0).astype(int)
    pivot.columns = [fmt_day(pd.Timestamp(c)) for c in pivot.columns]
    pivot = pivot.reset_index().rename(columns={"user": "User"})
    return pivot


def _section_pack(df: pd.DataFrame, label: str) -> str:
    buf = StringIO()
    buf.write(f"### {label}\n\n")
    if df.empty:
        buf.write("_(no events)_\n\n")
        return buf.getvalue()

    buf.write(f"Events: {fmt_int(len(df))} · Tokens: {fmt_tokens(df['Total Tokens'].sum())} · ")
    buf.write(f"People: {df['user'].nunique()} · On-demand $: ${df['cost_usd'].sum():.2f}\n\n")

    users = (
        df.groupby("user", as_index=False)
        .agg(events=("user", "size"), tokens=("Total Tokens", "sum"), spend=("cost_usd", "sum"))
        .sort_values("events", ascending=False)
    )
    users["tokens"] = users["tokens"].map(fmt_tokens)
    users["spend"] = users["spend"].map(lambda v: f"${v:.2f}")
    buf.write("**By person**\n\n")
    buf.write(_md_table(users.rename(columns={"user": "User", "events": "Events", "tokens": "Tokens", "spend": "On-demand $"})))
    buf.write("\n")

    daily_e = _daily_channel_pivot(df)
    if not daily_e.empty:
        buf.write("**Daily events by channel**\n\n")
        buf.write(_md_table(daily_e))
        buf.write("\n")

    daily_t = _daily_channel_pivot(df, "Total Tokens")
    if not daily_t.empty:
        buf.write("**Daily tokens by channel**\n\n")
        buf.write(_md_table(daily_t))
        buf.write("\n")

    fam = (
        df.groupby("family", as_index=False)
        .agg(events=("family", "size"), tokens=("Total Tokens", "sum"))
        .sort_values("events", ascending=False)
    )
    fam["tokens"] = fam["tokens"].map(fmt_tokens)
    buf.write("**Model family mix**\n\n")
    buf.write(_md_table(fam.rename(columns={"family": "Family", "events": "Events", "tokens": "Tokens"})))
    buf.write("\n")

    models = (
        df.groupby(["Model", "channel"], as_index=False, observed=True)
        .agg(events=("Model", "size"), tokens=("Total Tokens", "sum"), spend=("cost_usd", "sum"))
        .sort_values("events", ascending=False)
        .head(15)
    )
    models["tokens"] = models["tokens"].map(fmt_tokens)
    models["spend"] = models["spend"].map(lambda v: f"${v:.2f}")
    models["channel"] = models["channel"].astype(str)
    buf.write("**Top models**\n\n")
    buf.write(
        _md_table(
            models.rename(
                columns={
                    "Model": "Model",
                    "channel": "Channel",
                    "events": "Events",
                    "tokens": "Tokens",
                    "spend": "On-demand $",
                }
            )
        )
    )
    buf.write("\n")

    hourly = df.groupby("hour", as_index=False).size().rename(columns={"size": "events"})
    buf.write(f"**Hour of day ({TZ_LABEL})**\n\n")
    buf.write(_md_table(hourly.rename(columns={"hour": "Hour", "events": "Events"})))
    buf.write("\n")

    heat = _heatmap_table(df)
    if not heat.empty:
        buf.write(f"**Activity heatmap (events per user per {TZ_LABEL} day)**\n\n")
        buf.write(_md_table(heat))
        buf.write("\n")

    return buf.getvalue()


def build_ai_summary(
    df: pd.DataFrame,
    source_name: str,
    seat_emails: tuple[str, ...] = DEFAULT_SEAT_EMAILS,
    seat_cost_usd: float = DEFAULT_SEAT_COST_USD,
) -> str:
    """Markdown pack mirroring every chart grouping so an AI can compare tables at once."""
    spend = account_spend(df, seat_emails=seat_emails, seat_cost_usd=seat_cost_usd)
    start = df["Date_et"].min()
    end = df["Date_et"].max()
    buf = StringIO()

    buf.write("# Cursor / Grok Bot usage — AI analysis pack\n\n")
    buf.write(
        "Paste this into an AI chat. Tables below are the same groupings that populate the "
        "dashboard charts (Overview, Cursor IDE, Grok Bot, Compare). Look for trends that "
        "only show up when comparing multiple tables at once "
        "(e.g. events vs tokens by day, channel mix shifts by person, hour patterns vs spend).\n\n"
    )
    buf.write("## Meta\n\n")
    buf.write(f"- Source file: `{source_name}`\n")
    buf.write(f"- Range ({TZ_LABEL}): {fmt_when_year(start)} → {fmt_when_year(end)}\n")
    buf.write(f"- Events: {fmt_int(len(df))}\n")
    buf.write(f"- People in export: {df['user'].nunique()} ({', '.join(sorted(df['user'].unique()))})\n")
    buf.write(
        f"- Channel rule: `grok-bot-default` = Grok Bot; other models containing "
        f'"grok" = Cursor · Grok models; else Cursor · other models\n'
    )
    buf.write(
        f"- Seat assumption: {int(spend['seats'])} seats × ${seat_cost_usd:.0f}/mo "
        f"(not in CSV) + on-demand Cost from export\n\n"
    )

    buf.write("## Account spend\n\n")
    buf.write(f"- Team licenses: ${spend['license_usd']:,.2f}\n")
    buf.write(f"- On-demand usage: ${spend['on_demand_usd']:,.2f}\n")
    buf.write(f"- Est. month total: ${spend['total_usd']:,.2f}\n")
    buf.write(f"- Annualized run-rate: ${spend['annual_usd']:,.0f}\n\n")

    included_n = int((df["Kind"] == "Included").sum()) if "Kind" in df.columns else 0
    on_demand_n = int((df["Kind"] == "On-Demand").sum()) if "Kind" in df.columns else 0
    bot = int(df["is_grok_bot"].sum())
    cursor_grok = int((df["channel"] == "Cursor · Grok models").sum())
    cursor_other = int((df["channel"] == "Cursor · other models").sum())

    buf.write("## Headline KPIs\n\n")
    buf.write(f"- Total tokens: {fmt_tokens(df['Total Tokens'].sum())}\n")
    buf.write(f"- Included rows: {fmt_int(included_n)}\n")
    buf.write(f"- On-demand rows: {fmt_int(on_demand_n)}\n")
    buf.write(f"- Grok Bot events: {fmt_int(bot)}\n")
    buf.write(f"- Cursor · Grok events: {fmt_int(cursor_grok)}\n")
    buf.write(f"- Cursor · other events: {fmt_int(cursor_other)}\n")
    buf.write(f"- Calendar span: {fmt_day_year(start)} – {fmt_day_year(end)} {TZ_LABEL}\n\n")

    mix = (
        df.groupby("channel", as_index=False, observed=True)
        .agg(events=("channel", "size"), tokens=("Total Tokens", "sum"), spend=("cost_usd", "sum"))
        .sort_values("events", ascending=False)
    )
    mix["tokens"] = mix["tokens"].map(fmt_tokens)
    mix["spend"] = mix["spend"].map(lambda v: f"${v:.2f}")
    mix["channel"] = mix["channel"].astype(str)
    buf.write("## Product channel mix\n\n")
    buf.write(_md_table(mix.rename(columns={"channel": "Channel", "events": "Events", "tokens": "Tokens", "spend": "On-demand $"})))
    buf.write("\n")

    by_user_ch = (
        df.groupby(["user", "channel"], as_index=False, observed=True)
        .size()
        .rename(columns={"size": "events"})
    )
    by_user_ch_pivot = by_user_ch.pivot(index="user", columns="channel", values="events").fillna(0).astype(int)
    for ch in CHANNEL_ORDER:
        if ch not in by_user_ch_pivot.columns:
            by_user_ch_pivot[ch] = 0
    by_user_ch_pivot = by_user_ch_pivot[[c for c in CHANNEL_ORDER if c in by_user_ch_pivot.columns]]
    by_user_ch_pivot["Total"] = by_user_ch_pivot.sum(axis=1)
    by_user_ch_pivot = by_user_ch_pivot.reset_index().rename(columns={"user": "User"}).sort_values("Total", ascending=False)
    buf.write("## Who used which channel (events)\n\n")
    buf.write(_md_table(by_user_ch_pivot))
    buf.write("\n")

    daily_e = _daily_channel_pivot(df)
    buf.write("## Daily events by channel (full export)\n\n")
    buf.write(_md_table(daily_e))
    buf.write("\n")

    daily_t = _daily_channel_pivot(df, "Total Tokens")
    buf.write("## Daily tokens by channel (full export)\n\n")
    buf.write(_md_table(daily_t))
    buf.write("\n")

    users = (
        df.groupby(["user", "User"], as_index=False)
        .agg(
            events=("user", "size"),
            tokens=("Total Tokens", "sum"),
            output=("Output Tokens", "sum"),
            spend=("cost_usd", "sum"),
            models=("Model", "nunique"),
            grok_bot=("is_grok_bot", "sum"),
            cursor_grok=("channel", lambda s: int((s == "Cursor · Grok models").sum())),
            cursor_other=("channel", lambda s: int((s == "Cursor · other models").sum())),
            first=("Date_et", "min"),
            last=("Date_et", "max"),
        )
        .sort_values("events", ascending=False)
    )
    users_out = pd.DataFrame(
        {
            "User": users["user"],
            "Email": users["User"],
            "Events": users["events"],
            "Tokens": users["tokens"].map(fmt_tokens),
            "Cursor Grok": users["cursor_grok"].astype(int),
            "Grok Bot": users["grok_bot"].astype(int),
            "Cursor other": users["cursor_other"].astype(int),
            "Models": users["models"].astype(int),
            "On-demand $": users["spend"].map(lambda v: f"${v:.2f}"),
            f"First ({TZ_LABEL})": users["first"].map(lambda t: f"{fmt_when_year(t)} {TZ_LABEL}"),
            f"Last ({TZ_LABEL})": users["last"].map(lambda t: f"{fmt_when_year(t)} {TZ_LABEL}"),
        }
    )
    buf.write("## Per-user rollup\n\n")
    buf.write(_md_table(users_out))
    buf.write("\n")

    spend_user = (
        df.groupby("user", as_index=False)
        .agg(spend=("cost_usd", "sum"), events=("user", "size"))
        .sort_values("spend", ascending=False)
    )
    spend_user["spend"] = spend_user["spend"].map(lambda v: f"${v:.2f}")
    buf.write("## On-demand spend by person\n\n")
    buf.write(_md_table(spend_user.rename(columns={"user": "User", "spend": "On-demand $", "events": "Events"})))
    buf.write("\n")

    spend_model = (
        df[df["cost_usd"] > 0]
        .groupby(["Model", "channel"], as_index=False, observed=True)["cost_usd"]
        .sum()
        .sort_values("cost_usd", ascending=False)
        .head(15)
    )
    if spend_model.empty:
        buf.write("## On-demand spend by model\n\n_(no numeric Cost rows)_\n\n")
    else:
        spend_model["cost_usd"] = spend_model["cost_usd"].map(lambda v: f"${v:.2f}")
        spend_model["channel"] = spend_model["channel"].astype(str)
        buf.write("## On-demand spend by model\n\n")
        buf.write(
            _md_table(
                spend_model.rename(columns={"Model": "Model", "channel": "Channel", "cost_usd": "On-demand $"})
            )
        )
        buf.write("\n")

    kind = df.groupby("Kind", as_index=False).size().rename(columns={"size": "events"}) if "Kind" in df.columns else pd.DataFrame()
    if not kind.empty:
        buf.write("## Billing kind\n\n")
        buf.write(_md_table(kind.rename(columns={"Kind": "Kind", "events": "Events"})))
        buf.write("\n")

    heat = _heatmap_table(df)
    buf.write(f"## Activity heatmap (events · {TZ_LABEL} days)\n\n")
    buf.write(_md_table(heat))
    buf.write("\n")

    hourly = df.groupby("hour", as_index=False).size().rename(columns={"size": "events"})
    buf.write(f"## Hour of day ({TZ_LABEL}) — all channels\n\n")
    buf.write(_md_table(hourly.rename(columns={"hour": "Hour", "events": "Events"})))
    buf.write("\n")

    models = (
        df.groupby(["Model", "channel"], as_index=False, observed=True)
        .agg(events=("Model", "size"), tokens=("Total Tokens", "sum"), spend=("cost_usd", "sum"))
        .sort_values("events", ascending=False)
        .head(20)
    )
    models["tokens"] = models["tokens"].map(fmt_tokens)
    models["spend"] = models["spend"].map(lambda v: f"${v:.2f}")
    models["channel"] = models["channel"].astype(str)
    buf.write("## Top models (full export)\n\n")
    buf.write(
        _md_table(
            models.rename(
                columns={
                    "Model": "Model",
                    "channel": "Channel",
                    "events": "Events",
                    "tokens": "Tokens",
                    "spend": "On-demand $",
                }
            )
        )
    )
    buf.write("\n")

    fam = (
        df.groupby("family", as_index=False)
        .agg(events=("family", "size"), tokens=("Total Tokens", "sum"))
        .sort_values("events", ascending=False)
    )
    fam["tokens"] = fam["tokens"].map(fmt_tokens)
    buf.write("## Model family mix (full export)\n\n")
    buf.write(_md_table(fam.rename(columns={"family": "Family", "events": "Events", "tokens": "Tokens"})))
    buf.write("\n")

    seats_rows = []
    active = {str(u).lower() for u in df["User"].unique()}
    for email in seat_emails:
        short = email.split("@")[0]
        user_df = df[df["User"].str.lower() == email.lower()]
        seats_rows.append(
            {
                "Seat": short,
                "Email": email,
                "In export": "yes" if email.lower() in active else "no",
                "Events": len(user_df),
                "License $/mo": f"${seat_cost_usd:.0f}",
                "On-demand $": f"${float(user_df['cost_usd'].sum()):.2f}" if len(user_df) else "$0.00",
            }
        )
    buf.write("## Seat roster (assumed licenses)\n\n")
    buf.write(_md_table(pd.DataFrame(seats_rows)))
    buf.write("\n")

    buf.write("## Slice: Cursor IDE only (excludes grok-bot-default)\n\n")
    buf.write(_section_pack(df[df["is_cursor"]].copy(), "Cursor IDE"))

    buf.write("## Slice: Grok Bot only (model = grok-bot-default)\n\n")
    buf.write(_section_pack(df[df["is_grok_bot"]].copy(), "Grok Bot"))

    buf.write("## Suggested analysis prompt\n\n")
    buf.write(
        "Compare the tables above. Call out (1) who drives spend vs volume, "
        "(2) whether Grok Bot and Cursor Grok move together or separately by day/person, "
        "(3) days where tokens diverge from event counts, "
        "(4) hour-of-day or heatmap concentration risks, "
        "(5) anything that only appears when two groupings are read together. "
        "Keep recommendations practical for a Town IT team on Cursor Team seats.\n"
    )

    return buf.getvalue()
