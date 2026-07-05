"""Signal routing — scanner output to SQLite store + FCM push.

The router is the single fan-out point after the scanner emits a signal.
Phase 1 routes:
  1. SQLite write (``india_signals`` table) — consumed by API server
  2. FCM push notification — consumed by lumin-india-app

The scanner calls ``router.route(signals, suppressions)`` at the end of
each scan cycle.  The router persists both emitted signals and gate
suppressions for the API's ``/api/suppressed`` endpoint.
"""

from __future__ import annotations

from src import fcm_dispatcher
from src.scanner import Suppression
from src.signal_store import insert_signal, insert_suppression
from src.signals.model import IndiaSignal
from src.utils import get_logger

logger = get_logger("signal_router")


class IndiaSignalRouter:
    """Fan-out from scanner to persistence + delivery channels."""

    async def route(
        self,
        signals: list[IndiaSignal],
        suppressions: list[Suppression],
    ) -> None:
        for sig in signals:
            try:
                await insert_signal(sig)
                logger.info(
                    "stored {} {} {} conf={:.0f} tier={}",
                    sig.setup_class,
                    sig.direction,
                    sig.symbol,
                    sig.confidence,
                    sig.tier,
                )
            except Exception:
                logger.opt(exception=True).error(
                    "failed to store signal {}", sig.signal_id
                )
                continue

            try:
                await fcm_dispatcher.dispatch(sig)
            except Exception:
                logger.opt(exception=True).warning(
                    "FCM dispatch failed for {}", sig.signal_id
                )

        for sup in suppressions:
            try:
                await insert_suppression(
                    base=sup.base,
                    gate_name=sup.gate,
                    reason=sup.reason,
                    setup_class=sup.setup_class,
                    direction=sup.direction,
                    scan_time=sup.ts,
                )
            except Exception:
                logger.opt(exception=True).warning(
                    "failed to store suppression {}", sup.gate
                )
