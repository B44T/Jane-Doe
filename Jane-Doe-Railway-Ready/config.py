import glob, os, shutil
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.getenv("DISCORD_TOKEN", "")
APPLICATION_ID = int(os.getenv("APPLICATION_ID", "1528167247707770890"))
PUBLIC_KEY = os.getenv("DISCORD_PUBLIC_KEY", "cf11f135fec4aeb96aa989075f9c77e12f0dfc9d05316c4c5011cb88f404d875")
SECRET = os.getenv("DASHBOARD_SECRET", "change-me")
HOST = os.getenv("DASHBOARD_HOST", "0.0.0.0")
# Railway supplies PORT. DASHBOARD_PORT remains available for local Windows use.
PORT = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT", "5000"))
GUILD_ID = int(os.getenv("GUILD_ID", "0") or 0)
def local_data_dir():
    """Keep component callbacks outside replaceable download/version folders."""
    stable=os.path.join(os.path.expanduser("~"),".jane-doe-by-b4t")
    target_db=os.path.join(stable,"bot.db")
    if not os.path.isfile(target_db):
        downloads=os.path.join(os.path.expanduser("~"),"Downloads")
        candidates=[os.path.join(BASE_DIR,"data","bot.db")]
        candidates+=glob.glob(os.path.join(downloads,"Jane-Doe-by-B4T*","**","data","bot.db"),recursive=True)
        candidates=[p for p in candidates if os.path.isfile(p) and os.path.abspath(p)!=os.path.abspath(target_db)]
        if candidates:
            source=max(candidates,key=lambda p:(os.path.getsize(p),os.path.getmtime(p)))
            os.makedirs(stable,exist_ok=True); shutil.copy2(source,target_db)
            source_uploads=os.path.join(os.path.dirname(source),"uploads")
            if os.path.isdir(source_uploads):shutil.copytree(source_uploads,os.path.join(stable,"uploads"),dirs_exist_ok=True)
            print(f"Recovered persistent bot data from {source}")
    return stable

_configured_data=os.getenv("DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
if os.getenv("RAILWAY_ENVIRONMENT") or (_configured_data and os.path.isabs(_configured_data)):
    DATA_DIR=os.path.abspath(_configured_data or "/data")
else:
    DATA_DIR=local_data_dir()
DB_PATH = os.path.join(DATA_DIR, "bot.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
