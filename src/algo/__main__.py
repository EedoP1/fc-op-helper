# src/algo/__main__.py
"""Entry point for python -m src.algo <command>."""
import sys


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    # Remove the subcommand from argv so Click doesn't see it
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    if cmd == "scrape":
        from src.algo.scraper import main as scrape_main
        scrape_main()
    elif cmd == "run":
        from src.algo.engine import main as engine_main
        engine_main()
    elif cmd == "report":
        from src.algo.report import main as report_main
        report_main()
    else:
        print("Usage: python -m src.algo <command>")
        print()
        print("Commands:")
        print("  scrape   Fetch full price history from fut.gg")
        print("  run      Run backtests (--strategy NAME | --all)")
        print("  report   View backtest results (--strategy NAME, --sort COLUMN)")
        sys.exit(1)


if __name__ == "__main__":
    main()
