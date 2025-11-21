# app/alert_monitor.py
"""Price alert monitoring service."""

import logging
import time
from typing import Dict, List

from app.data import GateAdapter
from app.notifications import email_notifier
from app.storage import store

logger = logging.getLogger(__name__)


class PriceAlertMonitor:
    """Monitors active price alerts and triggers notifications."""

    def __init__(self, data_provider: GateAdapter):
        self.data = data_provider
        self.last_check_ts = 0
        self.check_interval = 60  # Check every 60 seconds

    def check_alerts(self) -> Dict[str, any]:
        """
        Check all active alerts and trigger notifications if conditions are met.

        Returns:
            Dictionary with check results: {
                'checked': int,
                'triggered': int,
                'errors': int,
                'timestamp': int
            }
        """
        now = int(time.time())
        results = {
            "checked": 0,
            "triggered": 0,
            "errors": 0,
            "timestamp": now,
        }

        # Get all active alerts
        alerts = store.get_active_price_alerts()
        if not alerts:
            logger.debug("No active price alerts to check")
            return results

        logger.info(f"Checking {len(alerts)} active price alerts")

        # Group alerts by symbol to minimize API calls
        alerts_by_symbol: Dict[str, List[dict]] = {}
        for alert in alerts:
            symbol = alert["symbol"]
            if symbol not in alerts_by_symbol:
                alerts_by_symbol[symbol] = []
            alerts_by_symbol[symbol].append(alert)

        # Check each symbol
        for symbol, symbol_alerts in alerts_by_symbol.items():
            try:
                # Get current price (use 1m bars for freshness)
                bars = self.data.history(symbol, "1m", limit=1)
                if not bars:
                    logger.warning(f"No price data available for {symbol}")
                    results["errors"] += len(symbol_alerts)
                    continue

                current_price = bars[-1].close
                logger.debug(f"{symbol} current price: {current_price}")

                # Check each alert for this symbol
                for alert in symbol_alerts:
                    results["checked"] += 1
                    alert_id = alert["id"]
                    target_price = alert["target_price"]
                    condition = alert["condition"]
                    email = alert["email"]

                    # Update last checked price
                    store.update_alert_last_checked_price(alert_id, current_price)

                    # Check if condition is met
                    triggered = False
                    if condition == "above" and current_price >= target_price:
                        triggered = True
                    elif condition == "below" and current_price <= target_price:
                        triggered = True

                    if triggered:
                        logger.info(
                            f"Alert {alert_id} triggered: {symbol} {condition} {target_price} "
                            f"(current: {current_price})"
                        )

                        # Send email notification
                        email_sent = email_notifier.send_price_alert(
                            to_email=email,
                            symbol=symbol,
                            target_price=target_price,
                            current_price=current_price,
                            condition=condition,
                        )

                        if email_sent:
                            # Mark alert as triggered
                            store.update_alert_status(
                                alert_id,
                                "triggered",
                                triggered_ts=now,
                                last_checked_price=current_price,
                            )
                            results["triggered"] += 1
                            logger.info(f"Alert {alert_id} marked as triggered and email sent")
                        else:
                            logger.error(f"Failed to send email for alert {alert_id}")
                            results["errors"] += 1

            except Exception as e:
                logger.error(f"Error checking alerts for {symbol}: {e}")
                results["errors"] += len(symbol_alerts)

        logger.info(
            f"Alert check complete: {results['checked']} checked, "
            f"{results['triggered']} triggered, {results['errors']} errors"
        )

        self.last_check_ts = now
        return results

    def should_check(self) -> bool:
        """Check if enough time has passed since last check."""
        return (time.time() - self.last_check_ts) >= self.check_interval

    def run_check_if_ready(self) -> Dict[str, any] | None:
        """
        Run alert check if enough time has passed since last check.

        Returns:
            Check results dict if check was run, None otherwise.
        """
        if self.should_check():
            return self.check_alerts()
        return None


# Module-level function for easy integration
def check_price_alerts(data_provider: GateAdapter) -> Dict[str, any]:
    """
    Convenience function to check price alerts.

    Args:
        data_provider: GateAdapter instance for fetching prices

    Returns:
        Dictionary with check results
    """
    monitor = PriceAlertMonitor(data_provider)
    return monitor.check_alerts()
