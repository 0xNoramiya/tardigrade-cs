"""Command-line entry point. `tardigrade serve` starts the FastAPI app.
`tardigrade chaos break <scenario>` toggles a chaos scenario for demos.
`tardigrade doctor` prints the current TF + chaos config so you can spot
misconfiguration before you start a demo."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from tardigrade import chaos as chaos_engine
from tardigrade.config import get_settings

console = Console()


@click.group()
def main() -> None:
    """TardigradeCS — resilient customer-service agent."""


@main.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8000, type=int)
@click.option("--reload", is_flag=True, help="Uvicorn auto-reload (dev only).")
def serve(host: str, port: int, reload: bool) -> None:
    """Start the FastAPI server (chat UI + /chat API + chaos panel)."""
    import uvicorn
    uvicorn.run("tardigrade.app:app", host=host, port=port, reload=reload, log_level="info")


@main.command()
def doctor() -> None:
    """Print TF + chaos config so you can sanity-check before a demo."""
    settings = get_settings()
    state = chaos_engine.ChaosState.load()
    t = Table(title="TardigradeCS · doctor", show_header=False)
    t.add_column("key", style="cyan")
    t.add_column("value")
    t.add_row("TFY_HOST", settings.tfy_host or "[red](unset)[/red]")
    t.add_row("TFY_GATEWAY_BASE_URL", settings.tfy_gateway_base_url or "[red](unset)[/red]")
    t.add_row("TFY_API_KEY", "[green]set[/green]" if settings.tfy_api_key else "[red](unset)[/red]")
    t.add_row("Primary Virtual Model", settings.tardigrade_primary_model)
    t.add_row("TFY_MCP_GATEWAY_URL", settings.tfy_mcp_gateway_url or "[yellow](unset — agent runs without tools)[/yellow]")
    t.add_row("Embedding model", settings.tardigrade_embedding_model)
    t.add_row("FAQ path", settings.tardigrade_faq_path)
    t.add_row("Similarity threshold", str(settings.tardigrade_embedding_threshold))
    t.add_row("Production guardrail", "[red]ON (chaos hard-disabled)[/red]" if settings.tardigrade_disable_chaos else "[green]off[/green]")
    t.add_row("Active chaos scenario", state.scenario or "[dim](none)[/dim]")
    t.add_row("Disabled tiers", ",".join(state.disabled_tiers) or "[dim](none)[/dim]")
    console.print(t)


@main.group()
def chaos() -> None:
    """Toggle chaos scenarios for demos."""


@chaos.command("list")
def chaos_list() -> None:
    """List all available chaos scenarios."""
    t = Table(title="Available scenarios")
    t.add_column("scenario", style="magenta")
    t.add_column("effect")
    for s in chaos_engine.MODEL_SCENARIOS:
        t.add_row(s, "swaps primary VM → pre-broken VM (TF fallback exercises)")
    for s, tiers in chaos_engine.TIER_DISABLE_SCENARIOS.items():
        t.add_row(s, f"disables tier(s): {', '.join(t.value for t in tiers)}")
    console.print(t)


@chaos.command("break")
@click.argument("scenario")
def chaos_break(scenario: str) -> None:
    """Activate a chaos scenario."""
    try:
        state = chaos_engine.activate(scenario)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise SystemExit(1)
    console.print(f"[yellow]chaos active:[/yellow] scenario={state.scenario or '-'} "
                  f"disabled_tiers={state.disabled_tiers or '-'}")


@chaos.command("clear")
def chaos_clear() -> None:
    """Clear all chaos state."""
    chaos_engine.clear()
    console.print("[green]chaos cleared[/green]")


@chaos.command("status")
def chaos_status() -> None:
    """Show current chaos state."""
    state = chaos_engine.ChaosState.load()
    console.print(f"scenario: {state.scenario or '(none)'}")
    console.print(f"disabled_tiers: {state.disabled_tiers or '(none)'}")
