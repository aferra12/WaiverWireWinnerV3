"""Waiver Wire Winner helpers.

Loading `.env` here means every entry point picks it up -- the web app, the CLI dry run
and the backtest all import from this package. `load_dotenv` does not override variables
that are already set, so the real environment on Cloud Run always wins, and the absence
of a .env file there is a no-op.
"""

from dotenv import load_dotenv

load_dotenv()
