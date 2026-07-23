import requests
import logging
import os

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self, config):
        """
        Initialize the Notifier using config dict.
        """
        self.config = config.get("notifications", {})
        self.enabled = self.config.get("enabled", False)
        self.channels = self.config.get("channels", [])
        
        # Load keys from environment
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = self.config.get("telegram", {}).get("chat_id") or os.getenv("TELEGRAM_CHAT_ID")
        self.serverchan_key = os.getenv("SERVERCHAN_SEND_KEY")
        self.bark_key = os.getenv("BARK_DEVICE_KEY")

        if self.enabled:
            logger.info(f"Notifier configured. Enabled channels: {self.channels}")
        else:
            logger.info("Notifier is disabled in configuration. Reports will only be logged locally.")

    def send_notification(self, title, content):
        """
        Orchestrate sending the report across enabled notification channels.
        Always saves the report locally in the workspace as 'latest_report.md'.
        """
        # Always write locally first
        local_filename = "latest_report.md"
        try:
            with open(local_filename, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n{content}")
            logger.info(f"Report saved locally to {local_filename}")
        except Exception as e:
            logger.error(f"Failed to save report locally: {e}")

        if not self.enabled or not self.channels:
            print("\n=== LATEST TRADING REPORT (LOCAL VIEW) ===")
            print(content)
            print("==========================================\n")
            return

        for channel in self.channels:
            if channel == "telegram":
                self._send_telegram(title, content)
            elif channel == "serverchan":
                self._send_serverchan(title, content)
            elif channel == "bark":
                self._send_bark(title, content)
            else:
                logger.warning(f"Unknown notification channel configured: {channel}")

    def _send_telegram(self, title, content):
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("Telegram token or Chat ID is missing. Skipping Telegram notification.")
            return

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        # Combine title and content
        message = f"**{title}**\n\n{content}"
        
        # First try to send as Markdown
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            result = response.json()
            if response.status_code == 200 and result.get("ok"):
                logger.info("Telegram notification sent successfully.")
            else:
                # If Markdown parsing failed, retry as HTML or plain text
                logger.warning(f"Telegram Markdown sending failed: {result.get('description')}. Retrying as plain text...")
                payload.pop("parse_mode")
                response = requests.post(url, json=payload, timeout=10)
                if response.status_code == 200:
                    logger.info("Telegram notification sent successfully as plain text.")
                else:
                    logger.error(f"Telegram sending failed: {response.text}")
        except Exception as e:
            logger.error(f"Error sending Telegram notification: {e}")

    def _send_serverchan(self, title, content):
        if not self.serverchan_key:
            logger.warning("Server酱 Send Key is missing. Skipping WeChat notification.")
            return
            
        url = f"https://sctapi.ftqq.com/{self.serverchan_key}.send"
        payload = {
            "title": title,
            "desp": content
        }
        try:
            response = requests.post(url, data=payload, timeout=10)
            result = response.json()
            if response.status_code == 200 and result.get("code") == 0:
                logger.info("WeChat (Server酱) notification sent successfully.")
            else:
                logger.error(f"Server酱 sending failed: {response.text}")
        except Exception as e:
            logger.error(f"Error sending Server酱 notification: {e}")

    def _send_bark(self, title, content):
        if not self.bark_key:
            logger.warning("Bark Device Key is missing. Skipping Bark notification.")
            return
            
        url = f"https://api.day.app/{self.bark_key}"
        payload = {
            "title": title,
            "body": content,
            "isArchive": 1,
            "group": "FeiyangAgent"
        }
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                logger.info("Bark notification sent successfully.")
            else:
                logger.error(f"Bark sending failed: {response.text}")
        except Exception as e:
            logger.error(f"Error sending Bark notification: {e}")
