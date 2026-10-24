"""Yahoo Finance DXY research pull. HTTP is not implemented.

The ticker is locked to ``DX-Y.NYB``. Store the print and that ticker.
Leave page HTML and bulk Yahoo dumps out of the repository. A Yahoo print
never opens the live hard gate. See ``docs/macro-rates-dxy.md``.
"""

from __future__ import annotations

from typing import NoReturn

from tools.macro.series import DXY_TICKER


def fetch_dxy_history() -> NoReturn:
    """Download daily ``DX-Y.NYB`` closes. The HTTP body is not implemented.

    TODO(Isaac): use ``yfinance`` inside this function (do not import it at
    module import if that pulls a network check). Request ticker
    ``DX-Y.NYB`` only. Keep the close as ``level`` and set ``ticker`` to
    ``DX-Y.NYB``. ``available_at`` is the bar close in America/New_York via
    :func:`tools.macro.availability.dxy_available_at`. This series is
    research-only. Do not persist HTML or a bulk dump in the repository.
    """
    raise NotImplementedError(
        f"Yahoo DXY fetch for {DXY_TICKER} is not implemented. "
        "See docs/macro-rates-dxy.md."
    )
