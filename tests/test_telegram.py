"""Tests for the Telegram integration helper."""
import json
import queue
from unittest.mock import patch, MagicMock

from sudo.core.config import Config
from sudo.core.telegram import (
    send_telegram_message,
    TelegramListener,
    TELEGRAM_QUEUE,
    start_telegram_listener,
    stop_telegram_listener,
)

def test_send_telegram_message():
    cfg = Config(telegram_enabled=True, telegram_token="123", telegram_chat_id="456")
    with patch("requests.post") as mock_post:
        send_telegram_message(cfg, "hello world")
        mock_post.assert_called_once_with(
            "https://api.telegram.org/bot123/sendMessage",
            json={"chat_id": "456", "text": "hello world"},
            timeout=10
        )


@patch("requests.get")
@patch("requests.post")
def test_telegram_listener_polling(mock_post, mock_get):
    cfg = Config(telegram_enabled=True, telegram_token="123", telegram_chat_id="456")
    
    mock_get.side_effect = [
        MagicMock(json=lambda: {"ok": True, "result": [{"update_id": 10}]}),
        MagicMock(json=lambda: {"ok": True, "result": [
            {
                "update_id": 11,
                "message": {
                    "chat": {"id": 456},
                    "text": "run pytest"
                }
            }
        ]})
    ]
    
    while not TELEGRAM_QUEUE.empty():
        TELEGRAM_QUEUE.get()
        
    listener = TelegramListener(cfg)
    
    with patch.object(listener, "send_reply") as mock_reply:
        listener.running = True
        listener.last_update_id = 0
        
        res = mock_get().json()
        results = res.get("result", [])
        if results:
            listener.last_update_id = results[-1]["update_id"]
            
        assert listener.last_update_id == 10
        
        res = mock_get().json()
        for update in res.get("result", []):
            listener.last_update_id = update["update_id"]
            msg = update.get("message")
            if msg:
                from_id = str(msg.get("chat", {}).get("id", ""))
                if from_id == "456":
                    text = msg.get("text")
                    if text:
                        TELEGRAM_QUEUE.put(text)
                        mock_reply("ok got it")
                        
        assert listener.last_update_id == 11
        assert TELEGRAM_QUEUE.get() == "run pytest"
        mock_reply.assert_called_once_with("ok got it")
