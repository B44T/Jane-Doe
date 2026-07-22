import json, os, sqlite3, threading
from config import DB_PATH

LOCK = threading.RLock()
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

def connect():
    db = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=20000")
    return db

def init_db():
    with LOCK, connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(guild_id INTEGER, key TEXT, value TEXT, PRIMARY KEY(guild_id,key));
        CREATE TABLE IF NOT EXISTS birthdays(guild_id INTEGER, user_id INTEGER, month INTEGER, day INTEGER, year INTEGER, timezone TEXT DEFAULT 'America/New_York', PRIMARY KEY(guild_id,user_id));
        CREATE TABLE IF NOT EXISTS warnings(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, user_id INTEGER, moderator_id INTEGER, reason TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS reaction_roles(guild_id INTEGER, message_id INTEGER, channel_id INTEGER, emoji TEXT, role_id INTEGER, PRIMARY KEY(message_id,emoji));
        CREATE TABLE IF NOT EXISTS glue(guild_id INTEGER, channel_id INTEGER PRIMARY KEY, content TEXT, embed_json TEXT, message_id INTEGER, enabled INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS glue_items(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, content TEXT, embed_json TEXT, message_id INTEGER, enabled INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS embed_archives(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, name TEXT NOT NULL, content TEXT, embed_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, channel_id INTEGER UNIQUE, owner_id INTEGER, panel_key TEXT, status TEXT DEFAULT 'open', created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS giveaways(message_id INTEGER PRIMARY KEY, guild_id INTEGER, channel_id INTEGER, prize TEXT, winners INTEGER, ends_at TEXT, ended INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS poll_votes(guild_id INTEGER, poll_key TEXT, user_id INTEGER, option_index INTEGER, PRIMARY KEY(guild_id,poll_key,user_id));
        CREATE TABLE IF NOT EXISTS confessions(id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER, user_id INTEGER, content TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS confession_replies(id INTEGER PRIMARY KEY AUTOINCREMENT, confession_id INTEGER, guild_id INTEGER, user_id INTEGER, content TEXT, message_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS bot_messages(message_id INTEGER PRIMARY KEY, guild_id INTEGER, channel_id INTEGER, content TEXT, embed_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS message_component_configs(message_id INTEGER PRIMARY KEY, guild_id INTEGER, component_key TEXT);
        """)
        for sql in ("ALTER TABLE birthdays ADD COLUMN announcement_channel_id INTEGER","ALTER TABLE birthdays ADD COLUMN announcement_message TEXT DEFAULT ''","ALTER TABLE confessions ADD COLUMN channel_id INTEGER","ALTER TABLE confessions ADD COLUMN message_id INTEGER","ALTER TABLE confessions ADD COLUMN thread_id INTEGER"):
            try:db.execute(sql)
            except sqlite3.OperationalError:pass
        db.execute("CREATE INDEX IF NOT EXISTS idx_confession_replies_parent ON confession_replies(confession_id,created_at)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_glue_items_channel ON glue_items(guild_id,channel_id,enabled)")
        if not db.execute("SELECT 1 FROM glue_items LIMIT 1").fetchone():
            db.execute("INSERT INTO glue_items(guild_id,channel_id,content,embed_json,message_id,enabled) SELECT guild_id,channel_id,content,embed_json,message_id,enabled FROM glue")
        # Identity lookup was removed; erase identities saved by older releases.
        db.execute("UPDATE confessions SET user_id=0 WHERE user_id!=0")
        db.execute("UPDATE confession_replies SET user_id=0 WHERE user_id!=0")
        # These were used only by the removed leaderboard/activity system.
        db.execute("DROP TABLE IF EXISTS message_activity")
        db.execute("DROP TABLE IF EXISTS xp")

def get_setting(guild_id, key, default=None):
    with LOCK, connect() as db:
        row=db.execute("SELECT value FROM settings WHERE guild_id=? AND key=?",(guild_id,key)).fetchone()
    if not row: return default
    try: return json.loads(row[0])
    except Exception: return row[0]

def set_setting(guild_id, key, value):
    raw=json.dumps(value)
    with LOCK, connect() as db:
        db.execute("INSERT INTO settings VALUES(?,?,?) ON CONFLICT(guild_id,key) DO UPDATE SET value=excluded.value",(guild_id,key,raw)); db.commit()

def rows(sql, args=()):
    with LOCK, connect() as db: return [dict(x) for x in db.execute(sql,args).fetchall()]

def execute(sql, args=()):
    with LOCK, connect() as db:
        cur=db.execute(sql,args); db.commit(); return cur.lastrowid
