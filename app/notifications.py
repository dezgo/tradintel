# app/notifications.py
"""Email notification service for price alerts."""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Handles sending email notifications via SMTP."""

    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.smtp_from_email = os.getenv("SMTP_FROM_EMAIL", self.smtp_username)
        self.enabled = bool(self.smtp_username and self.smtp_password)

        if not self.enabled:
            logger.warning(
                "Email notifications disabled: SMTP_USERNAME and SMTP_PASSWORD not configured"
            )

    def send_price_alert(
        self,
        to_email: str,
        symbol: str,
        target_price: float,
        current_price: float,
        condition: str,
    ) -> bool:
        """
        Send a price alert email.

        Args:
            to_email: Recipient email address
            symbol: Trading pair symbol (e.g., 'BTC_USDT')
            target_price: Target price that was set
            current_price: Current price that triggered the alert
            condition: 'above' or 'below'

        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.enabled:
            logger.warning(f"Email notification not sent to {to_email}: SMTP not configured")
            return False

        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Price Alert: {symbol} {condition.upper()} {target_price}"
            msg["From"] = self.smtp_from_email
            msg["To"] = to_email

            # Create plain text and HTML versions
            condition_text = "went above" if condition == "above" else "dropped below"
            text_body = f"""
Price Alert Triggered!

Symbol: {symbol}
Condition: Price {condition_text} {target_price}
Current Price: {current_price}

Your price alert has been triggered. Check your trading platform for more details.

---
TradinTel Price Alert System
"""

            html_body = f"""
<html>
  <head></head>
  <body>
    <h2 style="color: #2c3e50;">Price Alert Triggered!</h2>
    <table style="border-collapse: collapse; margin: 20px 0;">
      <tr>
        <td style="padding: 8px; font-weight: bold;">Symbol:</td>
        <td style="padding: 8px;">{symbol}</td>
      </tr>
      <tr>
        <td style="padding: 8px; font-weight: bold;">Condition:</td>
        <td style="padding: 8px;">Price {condition_text} {target_price}</td>
      </tr>
      <tr>
        <td style="padding: 8px; font-weight: bold;">Current Price:</td>
        <td style="padding: 8px; color: #e74c3c; font-size: 18px;">{current_price}</td>
      </tr>
    </table>
    <p style="color: #7f8c8d;">
      Your price alert has been triggered. Check your trading platform for more details.
    </p>
    <hr style="border: none; border-top: 1px solid #ecf0f1; margin: 20px 0;">
    <p style="color: #95a5a6; font-size: 12px;">TradinTel Price Alert System</p>
  </body>
</html>
"""

            # Attach both versions
            part1 = MIMEText(text_body, "plain")
            part2 = MIMEText(html_body, "html")
            msg.attach(part1)
            msg.attach(part2)

            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.smtp_from_email, to_email, msg.as_string())

            logger.info(f"Price alert email sent to {to_email} for {symbol}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error(f"SMTP authentication failed. Check SMTP_USERNAME and SMTP_PASSWORD")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error sending email to {to_email}: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending email to {to_email}: {e}")
            return False

    def send_test_email(self, to_email: str) -> bool:
        """
        Send a test email to verify SMTP configuration.

        Args:
            to_email: Recipient email address

        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.enabled:
            logger.warning("Cannot send test email: SMTP not configured")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "TradinTel Email Test"
            msg["From"] = self.smtp_from_email
            msg["To"] = to_email

            text_body = """
This is a test email from TradinTel.

If you received this, your email notifications are configured correctly!

---
TradinTel Price Alert System
"""

            html_body = """
<html>
  <head></head>
  <body>
    <h2 style="color: #2c3e50;">TradinTel Email Test</h2>
    <p>This is a test email from TradinTel.</p>
    <p style="color: #27ae60; font-weight: bold;">
      If you received this, your email notifications are configured correctly!
    </p>
    <hr style="border: none; border-top: 1px solid #ecf0f1; margin: 20px 0;">
    <p style="color: #95a5a6; font-size: 12px;">TradinTel Price Alert System</p>
  </body>
</html>
"""

            part1 = MIMEText(text_body, "plain")
            part2 = MIMEText(html_body, "html")
            msg.attach(part1)
            msg.attach(part2)

            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.sendmail(self.smtp_from_email, to_email, msg.as_string())

            logger.info(f"Test email sent to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Error sending test email: {e}")
            return False


# Singleton instance
email_notifier = EmailNotifier()
