"""Telegram bot messaging and command listener integration."""
from __future__ import annotations
import queue
import threading
import time
import requests
from sudo.core.config import Config

# Global queue for messages received from Telegram
TELEGRAM_QUEUE: queue.Queue[str] = queue.Queue()
_LISTENER: TelegramListener | None = None


def send_telegram_message(cfg: Config, text: str) -> None:
    if not cfg.telegram_enabled or not cfg.telegram_token or not cfg.telegram_chat_id:
        return
    url = f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage"
    payload = {
        "chat_id": cfg.telegram_chat_id,
        "text": text
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass


class TelegramListener(threading.Thread):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.running = True
        self.last_update_id = 0
        self.daemon = True

    def run(self):
        token = self.cfg.telegram_token
        chat_id = self.cfg.telegram_chat_id
        if not token or not chat_id:
            return

        # Fetch initial updates to skip stale messages
        try:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            res = requests.get(url, params={"timeout": 0}, timeout=5).json()
            results = res.get("result", [])
            if results:
                self.last_update_id = results[-1]["update_id"]
        except Exception:
            pass

        while self.running:
            try:
                url = f"https://api.telegram.org/bot{token}/getUpdates"
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 10
                }
                res = requests.get(url, params=params, timeout=15).json()
                if not res.get("ok"):
                    time.sleep(2)
                    continue

                for update in res.get("result", []):
                    self.last_update_id = update["update_id"]
                    msg = update.get("message")
                    if not msg:
                        continue

                    from_id = str(msg.get("chat", {}).get("id", ""))
                    if from_id != str(chat_id):
                        continue

                    text = msg.get("text")
                    if text:
                        TELEGRAM_QUEUE.put(text)
                        self.send_reply("ok got it")
            except Exception:
                time.sleep(2)

    def send_reply(self, text: str):
        url = f"https://api.telegram.org/bot{self.cfg.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.cfg.telegram_chat_id,
            "text": text
        }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception:
            pass

    def stop(self):
        self.running = False


def start_telegram_listener(cfg: Config) -> None:
    global _LISTENER
    if cfg.telegram_enabled and cfg.telegram_token and cfg.telegram_chat_id:
        if _LISTENER is None:
            _LISTENER = TelegramListener(cfg)
            _LISTENER.start()


def stop_telegram_listener() -> None:
    global _LISTENER
    if _LISTENER is not None:
        _LISTENER.stop()
        _LISTENER = None
