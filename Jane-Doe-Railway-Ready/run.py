import asyncio, logging, os, threading
import storage
from bot import bot
from dashboard import run_dashboard
import config

storage.init_db()

def dashboard_url():
    domain=os.getenv("RAILWAY_PUBLIC_DOMAIN","").strip()
    if domain:return f"https://{domain}"
    return f"http://127.0.0.1:{config.PORT}"

def bot_thread():
    loop=asyncio.new_event_loop(); asyncio.set_event_loop(loop)
    bot.loop_ref=loop
    bot.submit=lambda coro: asyncio.run_coroutine_threadsafe(coro,loop)
    try:
        loop.run_until_complete(bot.start(config.TOKEN))
    except Exception:
        logging.exception("Discord bot stopped unexpectedly")

def main():
    if not config.TOKEN or "PASTE" in config.TOKEN: raise SystemExit("Set DISCORD_TOKEN before starting the service.")
    if config.SECRET in ("change-me","replace-this-with-a-long-random-password"):
        raise SystemExit("Set DASHBOARD_SECRET to a new, long, private password before starting the service.")
    print(f"Starting Jane Doe by B4T on {config.HOST}:{config.PORT}")
    print(f"Dashboard: {dashboard_url()}")
    if os.getenv("RAILWAY_ENVIRONMENT"):
        print("Railway detected. Waiting for Discord before /health reports ready.")
    threading.Thread(target=bot_thread,daemon=True,name="discord-bot").start()
    run_dashboard()

if __name__=="__main__":main()
