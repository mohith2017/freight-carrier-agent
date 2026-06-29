"""
Typer CLI for the freight agent pipeline.

Current supported commands:
  freight init-db          create schema on all target stores
  freight load             load structured artifacts into all target stores
  freight verify           report row counts + a sample flat->per-mile rate check
  freight ingest all       run the whole pipeline (emails->calls->reconcile->embed)
  freight ingest emails    parse carrier_emails.json -> comm_events + offers
  freight ingest calls     transcribe + extract call recordings (cached)
  freight reconcile        link comm_events to carriers/loads, flag cross-channel
  freight embed            chunk + embed communications into knowledge_chunks
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import func, select

from freight_agent.config import get_settings
from freight_agent.db import init_schema, primary_engine, session_factory, target_engines
from freight_agent.db.models import Carrier, CommEvent, Load, Offer, RateHistory
from freight_agent.ingestion.loaders import load_all
from freight_agent.rates import assess_offer, flat_to_per_mile

app = typer.Typer(help="Goodlane freight carrier intake assistant CLI.")
ingest_app = typer.Typer(help="Ingest multi-modal carrier communications.")
app.add_typer(ingest_app, name="ingest")
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


def _primary_session():
    settings = get_settings()
    engine = primary_engine(settings)
    init_schema(engine)
    return settings, session_factory(engine)


@ingest_app.command("emails")
def ingest_emails_cmd(
    llm: bool = typer.Option(
        True, "--llm/--no-llm", help="Layer GPT extraction on the deterministic pass."
    ),
    incremental: bool = typer.Option(
        False, "--incremental", help="Only add emails not already ingested."
    ),
) -> None:
    from freight_agent.ingestion.parse_emails import ingest_emails

    settings, Session = _primary_session()
    extractor = None
    if llm and settings.openai_api_key:
        from freight_agent.ingestion.llm import OpenAIExtractor

        extractor = OpenAIExtractor(settings)
    elif llm:
        console.print("[yellow]no OPENAI_API_KEY -> deterministic-only extraction[/yellow]")
    with Session() as session:
        counts = ingest_emails(
            session, settings.dataset_path, extractor=extractor, incremental=incremental
        )
    mode = "deterministic+llm" if extractor else "deterministic"
    if incremental:
        mode += ", incremental"
    console.print(f"[green]emails ingested[/green] ({mode}): {counts}")


@ingest_app.command("calls")
def ingest_calls_cmd(
    llm: bool = typer.Option(True, "--llm/--no-llm"),
    incremental: bool = typer.Option(
        False, "--incremental", help="Only add call recordings not already ingested."
    ),
) -> None:
    from freight_agent.ingestion.transcribe_calls import ingest_calls

    settings, Session = _primary_session()
    transcriber = None
    extractor = None
    if settings.openai_api_key:
        from freight_agent.ingestion.llm import OpenAIExtractor, OpenAITranscriber

        transcriber = OpenAITranscriber(settings)
        if llm:
            extractor = OpenAIExtractor(settings)
    else:
        console.print(
            "[yellow]no OPENAI_API_KEY -> using cached transcripts only[/yellow]"
        )
    with Session() as session:
        counts = ingest_calls(
            session,
            settings.dataset_path,
            settings.transcripts_path,
            transcriber=transcriber,
            extractor=extractor,
            incremental=incremental,
        )
    console.print(f"[green]calls ingested[/green]: {counts}")


@ingest_app.command("all")
def ingest_all_cmd(
    llm: bool = typer.Option(True, "--llm/--no-llm"),
    skip_calls: bool = typer.Option(
        False, "--skip-calls", help="Skip transcription (emails-only run)."
    ),
    incremental: bool = typer.Option(
        False,
        "--incremental",
        help="Only ingest/embed new records (for folding in a newer dataset).",
    ),
) -> None:
    from freight_agent.ingestion.embed import embed_events
    from freight_agent.ingestion.parse_emails import ingest_emails
    from freight_agent.ingestion.reconcile import reconcile
    from freight_agent.ingestion.transcribe_calls import ingest_calls

    settings, Session = _primary_session()
    has_key = bool(settings.openai_api_key)
    extractor = None
    transcriber = None
    if has_key:
        from freight_agent.ingestion.llm import OpenAIExtractor, OpenAITranscriber

        if llm:
            extractor = OpenAIExtractor(settings)
        transcriber = OpenAITranscriber(settings)
    else:
        console.print(
            "[yellow]no OPENAI_API_KEY -> deterministic emails, cached transcripts, "
            "no embeddings[/yellow]"
        )

    mode = " [dim](incremental)[/dim]" if incremental else ""
    with Session() as session:
        console.print(f"[bold]1/4 emails[/bold]{mode}")
        e = ingest_emails(
            session, settings.dataset_path, extractor=extractor, incremental=incremental
        )
        console.print(f"  {e}")

        if skip_calls:
            console.print("[bold]2/4 calls[/bold] [yellow]skipped[/yellow]")
        else:
            console.print(f"[bold]2/4 calls[/bold] (transcribe + extract){mode}")
            c = ingest_calls(
                session,
                settings.dataset_path,
                settings.transcripts_path,
                transcriber=transcriber,
                extractor=extractor,
                incremental=incremental,
            )
            console.print(f"  {c}")

        console.print("[bold]3/4 reconcile[/bold]")
        report = reconcile(session)
        console.print(
            f"  carrier-linked {report.carrier_linked}, load-linked "
            f"{report.load_linked}, cross-channel {len(report.cross_channel_carrier_ids)}"
        )

        console.print(f"[bold]4/4 embed[/bold]{mode}")
        if has_key:
            from freight_agent.ingestion.llm import OpenAIEmbedder

            emb = embed_events(session, OpenAIEmbedder(settings), incremental=incremental)
            console.print(f"  {emb}")
        else:
            console.print("  [yellow]skipped (needs OPENAI_API_KEY)[/yellow]")

    console.print("\n[green]pipeline complete[/green]")
    verify_ingest()


@app.command("reconcile")
def reconcile_cmd() -> None:
    from freight_agent.ingestion.reconcile import reconcile

    _, Session = _primary_session()
    with Session() as session:
        report = reconcile(session)
    console.print(
        f"[green]reconciled[/green] {report.events} events; "
        f"carrier-linked {report.carrier_linked}, load-linked {report.load_linked}"
    )
    console.print(f"  methods: {report.by_method}")
    console.print(
        f"  cross-channel carriers: {len(report.cross_channel_carrier_ids)} "
        f"{report.cross_channel_carrier_ids}"
    )


@app.command("embed")
def embed_cmd(
    incremental: bool = typer.Option(
        False, "--incremental", help="Only embed events that have no chunks yet."
    ),
) -> None:
    from freight_agent.ingestion.embed import embed_events

    settings, Session = _primary_session()
    if not settings.openai_api_key:
        console.print("[red]embed requires OPENAI_API_KEY[/red]")
        raise typer.Exit(code=1)
    from freight_agent.ingestion.llm import OpenAIEmbedder

    embedder = OpenAIEmbedder(settings)
    with Session() as session:
        counts = embed_events(session, embedder, incremental=incremental)
    console.print(f"[green]embedded[/green]: {counts}")


@app.command("verify-ingest")
def verify_ingest() -> None:
    _, Session = _primary_session()
    table = Table(title="Ingestion counts (primary store)")
    table.add_column("metric")
    table.add_column("count", justify="right")
    with Session() as session:
        emails = session.scalar(
            select(func.count()).select_from(CommEvent).where(
                CommEvent.source_type == "email"
            )
        )
        calls = session.scalar(
            select(func.count()).select_from(CommEvent).where(
                CommEvent.source_type == "call"
            )
        )
        offers = session.scalar(select(func.count()).select_from(Offer))
        linked = session.scalar(
            select(func.count()).select_from(CommEvent).where(
                CommEvent.carrier_id.isnot(None)
            )
        )
        for name, val in [
            ("email events", emails),
            ("call events", calls),
            ("offers", offers),
            ("carrier-linked events", linked),
        ]:
            table.add_row(name, str(val or 0))
    console.print(table)


if __name__ == "__main__":
    app()
