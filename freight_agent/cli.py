"""
Typer CLI for the freight agent pipeline.

Current supported commands:
  freight init-db     create schema on all target stores
  freight load        load structured artifacts into all target stores
  freight verify      report row counts + a sample flat->per-mile rate check
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from freight_agent.config import get_settings
from freight_agent.db import init_schema, primary_engine, session_factory, target_engines
from freight_agent.ingestion.loaders import load_all
from freight_agent.models import Carrier, Load, RateHistory
from freight_agent.rates import assess_offer, flat_to_per_mile

app = typer.Typer(help="Goodlane freight carrier intake assistant CLI.")
console = Console()

EXPECTED = {"loads": 50, "carriers": 48, "rate_history": 720}


@app.command("init-db")
def init_db() -> None:
    settings = get_settings()
    for engine in target_engines(settings):
        init_schema(engine)
        target = engine.url.render_as_string(hide_password=True)
        console.print(f"[green]schema ready[/green] -> {target}")


@app.command("load")
def load() -> None:
    settings = get_settings()
    dataset = settings.dataset_path
    if not dataset.exists():
        console.print(f"[red]dataset not found:[/red] {dataset}")
        raise typer.Exit(code=1)

    for engine in target_engines(settings):
        init_schema(engine)
        counts = load_all(engine, dataset)
        target = engine.url.render_as_string(hide_password=True)
        console.print(f"[green]loaded[/green] {counts} -> {target}")


@app.command("verify")
def verify() -> None:
    settings = get_settings()
    engine = primary_engine(settings)
    Session = session_factory(engine)

    table = Table(title="Row counts (primary store)")
    table.add_column("table")
    table.add_column("count", justify="right")
    table.add_column("expected", justify="right")
    table.add_column("ok", justify="center")

    ok_all = True
    with Session() as session:
        actual = {
            "loads": session.scalar(select(func.count()).select_from(Load)),
            "carriers": session.scalar(select(func.count()).select_from(Carrier)),
            "rate_history": session.scalar(select(func.count()).select_from(RateHistory)),
        }
        for name, expected in EXPECTED.items():
            got = actual[name] or 0
            ok = got == expected
            ok_all = ok_all and ok
            mark = "[green]ok[/green]" if ok else "[red]FAIL[/red]"
            table.add_row(name, str(got), str(expected), mark)
        console.print(table)

        sample = session.scalars(
            select(Load).where(Load.distance_miles.isnot(None)).limit(1)
        ).first()
        if sample:
            per_mile = flat_to_per_mile(sample.offered_rate_usd, sample.distance_miles)
            market = session.scalars(
                select(RateHistory).where(
                    RateHistory.origin_state == sample.origin_state,
                    RateHistory.destination_state == sample.destination_state,
                    RateHistory.equipment_type == sample.equipment_type,
                )
            ).first()
            avg = market.avg_rate_per_mile if market else None
            verdict = assess_offer(sample.offered_rate_usd, sample.distance_miles, avg)
            lane = f"{sample.origin_state}->{sample.destination_state}, {sample.equipment_type}"
            console.print(
                f"\n[bold]Rate check[/bold] load {sample.load_id} ({lane}): "
                f"${sample.offered_rate_usd} / {sample.distance_miles}mi = "
                f"[cyan]{per_mile}/mi[/cyan]; market avg {avg}/mi -> "
                f"[yellow]{verdict.position}[/yellow]"
            )

    target = engine.url.render_as_string(hide_password=True)
    console.print(f"\nprimary store: {target}")
    if not ok_all:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
