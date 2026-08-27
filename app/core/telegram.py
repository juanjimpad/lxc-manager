import httpx

from . import config


def notify(text: str) -> None:
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except httpx.HTTPError:
        pass  # a failed notification must not take the run down with it
