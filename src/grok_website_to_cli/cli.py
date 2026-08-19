"""
CLI Entry Point
~~~~~~~~~~~~~~~~

Async command-line interface for chatgpt-website-to-cli.
Starts a local WebSocket server, waits for the paired browser extension
to connect, then orchestrates the Chatgpt interaction pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from chatgpt_website_to_cli import __version__
from chatgpt_website_to_cli.browser import ChatgptBridge, DEFAULT_PORT
from chatgpt_website_to_cli.chatgpt import ChatgptAutomation

console = Console()


def _setup_logging(verbose: bool) -> None:
    """Configure logging with Rich handler for pretty terminal output."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )
    # Suppress noisy library loggers
    logging.getLogger("websockets").setLevel(logging.WARNING)


def _read_prompt_file(filepath: str) -> str:
    """Read and validate the prompt file."""
    path = Path(filepath).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {path}")

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Prompt file is empty: {path}")

    return text


def _write_output(content: str, output_path: str | None) -> None:
    """Write extracted content to file or display in terminal."""
    if output_path:
        path = Path(output_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(content)
            f.write("\n")
        console.print(f"\n[green]+[/green] Output appended to [bold]{path}[/bold]")
    else:
        console.print()
        console.print(
            Panel(
                Syntax(content, "text", theme="monokai", word_wrap=True),
                title="[bold cyan]Chatgpt Response (Last Code Block)[/bold cyan]",
                border_style="cyan",
                padding=(1, 2),
            )
        )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI."""
    parser = argparse.ArgumentParser(
        prog="chatgpt-cli",
        description=(
            "Chatgpt Website-to-CLI -- Send prompts to Chatgpt via browser "
            "extension bridge and extract code block responses."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  chatgpt-cli prompt.txt                    # Display result in terminal\n"
            "  chatgpt-cli prompt.txt -o output.py       # Append result to output.py\n"
            "  chatgpt-cli prompt.txt -o out.py -w 300   # Wait up to 5 minutes\n"
            "  chatgpt-cli prompt.txt --browser chrome   # Use Chrome instead of Edge\n"
            "  chatgpt-cli prompt.txt -v                 # Verbose logging\n"
        ),
    )
    parser.add_argument(
        "prompt_file",
        help="Path to a text file containing the prompt to send to Chatgpt.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help="Path to output file. Result will be appended. "
        "If not specified, output is printed to terminal.",
    )
    parser.add_argument(
        "-w",
        "--max-wait",
        type=int,
        default=180,
        metavar="SECONDS",
        help="Maximum seconds to wait for Chatgpt response (default: 180).",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"WebSocket bridge port (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--full-response",
        action="store_true",
        help="Extract the full response text instead of just the last code block.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose/debug logging.",
    )
    parser.add_argument(
        "-b",
        "--browser",
        choices=["edge", "chrome"],
        default="edge",
        help="Browser to use (default: edge).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


async def async_main(args: argparse.Namespace) -> int:
    """Async main pipeline.

    1. Start WebSocket server
    2. Wait for extension to connect
    3. Find/open Chatgpt tab
    4. Send prompt
    5. Wait for response
    6. Extract code block
    7. Output result
    """
    # ── Read prompt ───────────────────────────────────────────────────
    try:
        prompt_text = _read_prompt_file(args.prompt_file)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        return 1

    console.print(
        f"[dim]Prompt loaded ({len(prompt_text)} chars) from:[/dim] {args.prompt_file}"
    )

    # ── Start bridge server ───────────────────────────────────────────
    browser_name = "Chrome" if args.browser == "chrome" else "Edge"
    extensions_url = "chrome://extensions" if args.browser == "chrome" else "edge://extensions"

    bridge = ChatgptBridge(port=args.port, browser_name=browser_name)

    try:
        await bridge.start()
        console.print(
            f"[green]+[/green] WebSocket bridge running on "
            f"[bold]ws://127.0.0.1:{args.port}[/bold]"
        )

        # ── Wait for extension ────────────────────────────────────────
        with console.status(
            "[bold blue]Waiting for Chatgpt CLI Bridge extension to connect...[/bold blue]\n"
            f"[dim]Make sure the extension is installed and enabled in {browser_name}[/dim]",
            spinner="dots",
        ):
            try:
                await bridge.wait_for_extension(timeout=60)
            except TimeoutError as exc:
                console.print(f"\n[red]Error:[/red] {exc}")
                console.print(
                    "\n[yellow]Setup:[/yellow] Load the extension from the "
                    "[bold]extension/[/bold] folder:\n"
                    f"  1. Open [bold]{extensions_url}[/bold]\n"
                    "  2. Enable [bold]Developer mode[/bold]\n"
                    "  3. Click [bold]Load unpacked[/bold] and select the "
                    "[bold]extension/[/bold] directory\n"
                )
                return 1

        console.print("[green]+[/green] Extension connected")

        # ── Find or open Chatgpt tab ────────────────────────────────────
        chatgpt = ChatgptAutomation(
            bridge=bridge,
            max_wait_seconds=args.max_wait,
        )

        with console.status("[bold blue]Finding or opening Chatgpt tab...[/bold blue]"):
            await chatgpt.find_or_open_chatgpt_tab()
        console.print("[green]+[/green] Chatgpt tab is active")

        # Wait for page to be ready after tab switch
        await asyncio.sleep(2)

        # ── Send prompt ───────────────────────────────────────────────
        with console.status("[bold blue]Sending prompt to Chatgpt...[/bold blue]"):
            await chatgpt.send_prompt(prompt_text)
        console.print("[green]+[/green] Prompt submitted")

        # ── Wait for response ─────────────────────────────────────────
        with console.status(
            f"[bold yellow]Waiting for Chatgpt response "
            f"(up to {args.max_wait}s)...[/bold yellow]",
            spinner="dots",
        ):
            await chatgpt.wait_for_response()
        console.print("[green]+[/green] Response received")

        # ── Extract result ────────────────────────────────────────────
        with console.status("[bold blue]Extracting code block...[/bold blue]"):
            if args.full_response:
                result = await chatgpt.extract_full_response()
            else:
                result = await chatgpt.extract_last_code_block()

        if result is None and not args.full_response:
            console.print(
                "[yellow]![/yellow] No code blocks found. "
                "Trying full response extraction..."
            )
            result = await chatgpt.extract_full_response()

        if result is None:
            console.print(
                "[red]x[/red] Could not extract any response from Chatgpt. "
                "The page layout may have changed, or the response was empty."
            )
            return 1

        # ── Output ────────────────────────────────────────────────────
        _write_output(result, args.output)
        console.print("\n[bold green]Done![/bold green]")
        return 0

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        return 130
    except Exception as exc:
        logging.getLogger(__name__).exception("An error occurred:")
        console.print(f"\n[red]Error:[/red] {exc}")
        return 1
    finally:
        await bridge.stop()


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the chatgpt-cli command."""
    parser = build_parser()
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    # ── Banner ────────────────────────────────────────────────────────
    console.print()
    console.print(
        Panel(
            Text.from_markup(
                "[bold cyan]Chatgpt Website-to-CLI[/bold cyan]\n"
                f"[dim]v{__version__} | Extension-bridged RPA automation[/dim]"
            ),
            border_style="bright_blue",
            padding=(1, 4),
        )
    )
    console.print()

    return asyncio.run(async_main(args))


if __name__ == "__main__":
    sys.exit(main())
