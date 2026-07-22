import asyncio, io, json, secrets, os, re, uuid
from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
import config, storage, discord, requests
from PIL import Image, ImageOps, ImageSequence
from bot import bot, TicketPanel, PollView, ReactionRoleView, ActionButtonView, GlueTemplateView
from embed_utils import make_embed, has_embed_content, embed_to_dict, make_embed_with_files

app=Flask(__name__); app.secret_key=config.SECRET
app.wsgi_app=ProxyFix(app.wsgi_app,x_for=1,x_proto=1,x_host=1)
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE="Lax",SESSION_COOKIE_SECURE=bool(os.getenv("RAILWAY_ENVIRONMENT")))
UPLOAD_DIR=config.UPLOAD_DIR; os.makedirs(UPLOAD_DIR,exist_ok=True)
app.config["MAX_CONTENT_LENGTH"]=10*1024*1024
# Dashboard JavaScript changes frequently; always make browsers revalidate it so
# newly added controls do not disappear behind a stale cached bundle.
app.config["SEND_FILE_MAX_AGE_DEFAULT"]=0

@app.get("/health")
def health():
    ready=bot.is_ready()
    return jsonify(status="ok",discord_ready=ready),200

def protected(fn):
    @wraps(fn)
    def inner(*a,**kw):
        if not session.get("ok"): return redirect(url_for("login"))
        return fn(*a,**kw)
    return inner

@app.route("/login",methods=["GET","POST"])
def login():
    if request.method=="POST" and secrets.compare_digest(request.form.get("password",""),config.SECRET): session["ok"]=True; return redirect("/")
    return render_template("login.html",error=request.method=="POST")

@app.get("/logout")
def logout(): session.clear(); return redirect("/login")

def guilds(): return [g for g in bot.guilds if not config.GUILD_ID or g.id==config.GUILD_ID]
def guild(gid):
    gid=int(gid)
    if config.GUILD_ID and gid!=config.GUILD_ID:return None
    return bot.get_guild(gid)
async def register_view(view):bot.add_view(view); return True

@app.get("/")
@protected
def home(): return render_template("dashboard.html",guilds=guilds(),application_id=config.APPLICATION_ID)

@app.get("/api/guild/<int:gid>/context")
@protected
def context(gid):
    g=guild(gid)
    if not g:return jsonify(error="Bot is not connected to that server"),404
    # discord.py keeps this updated from gateway events, including new emojis.
    # Reading it avoids a Discord REST request every time the dashboard refreshes.
    emojis=list(g.emojis)
    static=sum(not e.animated for e in emojis); animated=sum(e.animated for e in emojis); limit=g.emoji_limit
    return jsonify(guild={"id":str(g.id),"name":g.name,"icon":str(g.icon.url) if g.icon else None},bot_profile={"name":g.me.display_name,"avatar":str(g.me.display_avatar.url)},emoji_capacity={"limit_per_type":limit,"static_used":static,"static_available":max(0,limit-static),"animated_used":animated,"animated_available":max(0,limit-animated)},channels=[{"id":str(c.id),"name":c.name,"type":str(c.type)} for c in g.channels if hasattr(c,"name")],roles=[{"id":str(r.id),"name":r.name,"color":str(r.color)} for r in g.roles if not r.is_default()],emojis=[emoji_json(e) for e in emojis])

def emoji_json(e):return {"id":str(e.id),"name":e.name,"url":str(e.url),"proxy_url":str(e.url),"animated":e.animated,"text":str(e)}

def saved_components(gid,message_id):
    row=storage.rows("SELECT component_key FROM message_component_configs WHERE message_id=? AND guild_id=?",(message_id,gid))
    return storage.get_setting(gid,f"message_components:{row[0]['component_key']}",{}) if row else {}

def message_components(gid,msg):
    saved=saved_components(gid,msg.id)
    if saved:return saved
    buttons=[]; menus=[]
    for action_row in msg.components:
        for child in getattr(action_row,"children",[]):
            custom_id=getattr(child,"custom_id",None) or ""
            match=re.match(r"action(?:menu)?:([^:]+):",custom_id)
            if match:
                key=match.group(1); cfg=storage.get_setting(gid,f"message_components:{key}",{})
                if cfg:
                    storage.execute("INSERT OR REPLACE INTO message_component_configs VALUES(?,?,?)",(msg.id,gid,key)); return cfg
            if isinstance(child,discord.Button):
                styles={1:"primary",2:"secondary",3:"success",4:"danger",5:"link"}; buttons.append({"label":child.label or "Button","emoji":str(child.emoji) if child.emoji else "","style":styles.get(child.style.value,"secondary"),"action":"link" if child.url else "response","url":child.url or "","response":"","ephemeral":True})
            elif isinstance(child,discord.SelectMenu):
                menus.append({"placeholder":child.placeholder or "Choose an option","options":[{"label":o.label,"description":o.description or "","emoji":str(o.emoji) if o.emoji else "","response":"","ephemeral":True} for o in child.options]})
    return {"buttons":buttons,"menus":menus}

@app.get("/api/emoji/<int:emoji_id>")
@protected
def emoji_image(emoji_id):
    animated=request.args.get("animated")=="1"; exts=("gif","webp","png") if animated else ("webp","png")
    for ext in exts:
        try:
            upstream=requests.get(f"https://cdn.discordapp.com/emojis/{emoji_id}.{ext}?size=96&quality=lossless",headers={"User-Agent":"Jane-Doe-by-B4T/1.0"},timeout=8)
            if upstream.ok:return Response(upstream.content,content_type=upstream.headers.get("Content-Type",f"image/{ext}"),headers={"Cache-Control":"public, max-age=3600"})
        except requests.RequestException:pass
    return Response(status=404)

@app.get("/api/guild/<int:gid>/emojis")
@protected
def emojis(gid):
    g=guild(gid)
    if not g:return jsonify(error="Bot is not connected to that server"),404
    items=list(g.emojis)
    static=sum(not e.animated for e in items); animated=sum(e.animated for e in items); limit=g.emoji_limit
    return jsonify(emojis=[emoji_json(e) for e in items],capacity={"limit_per_type":limit,"static_used":static,"static_available":max(0,limit-static),"animated_used":animated,"animated_available":max(0,limit-animated)})

def resized_emoji(raw,size):
    source=Image.open(io.BytesIO(raw)); size=max(32,min(int(size or 128),128))
    def frame_canvas(frame,target):
        frame=frame.convert("RGBA"); frame.thumbnail((target,target),Image.Resampling.LANCZOS); canvas=Image.new("RGBA",(target,target),(0,0,0,0)); canvas.alpha_composite(frame,((target-frame.width)//2,(target-frame.height)//2)); return canvas
    animated=getattr(source,"is_animated",False)
    target=size
    while target>=32:
        out=io.BytesIO()
        if animated:
            frames=[frame_canvas(f.copy(),target) for f in ImageSequence.Iterator(source)]; durations=[f.info.get("duration",source.info.get("duration",80)) for f in ImageSequence.Iterator(source)]; frames[0].save(out,"GIF",save_all=True,append_images=frames[1:],duration=durations,loop=source.info.get("loop",0),disposal=2,optimize=True)
        else:frame_canvas(source,target).save(out,"PNG",optimize=True)
        if out.tell()<=256*1024:return out.getvalue(),animated,target
        target=int(target*.8)
    raise ValueError("The resized emoji is still over Discord's 256 KB limit. Use a shorter GIF or smaller file.")

@app.post("/api/guild/<int:gid>/emoji/upload")
@protected
def emoji_upload(gid):
    g=guild(gid); f=request.files.get("file"); name=re.sub(r"[^A-Za-z0-9_]","_",request.form.get("name","").strip())[:32]
    if not f or not f.filename:return jsonify(error="Choose an emoji image."),400
    if len(name)<2:return jsonify(error="Emoji names need at least two letters, numbers, or underscores."),400
    try:data,animated,actual_size=resized_emoji(f.read(),request.form.get("size",128))
    except (ValueError,OSError) as e:return jsonify(error=str(e)),400
    async def work():return await g.create_custom_emoji(name=name,image=data,reason="Uploaded from Jane Doe dashboard")
    try:e=bot.submit(work()).result(20)
    except discord.Forbidden:return jsonify(error="Give the bot Manage Expressions permission."),403
    except discord.HTTPException as e:return jsonify(error=e.text or "Discord rejected the emoji."),400
    return jsonify(ok=True,emoji=emoji_json(e),animated=animated,size=actual_size)

@app.post("/api/guild/<int:gid>/emoji/<int:emoji_id>/rename")
@protected
def emoji_rename(gid,emoji_id):
    g=guild(gid); name=re.sub(r"[^A-Za-z0-9_]","_",(request.get_json(force=True).get("name") or "").strip())[:32]; emoji=discord.utils.get(g.emojis,id=emoji_id)
    if not emoji:return jsonify(error="Emoji not found."),404
    if len(name)<2:return jsonify(error="Emoji names need at least two letters, numbers, or underscores."),400
    async def work():return await emoji.edit(name=name,reason="Renamed from Jane Doe dashboard")
    try:e=bot.submit(work()).result(15)
    except discord.Forbidden:return jsonify(error="Give the bot Manage Expressions permission."),403
    except discord.HTTPException as e:return jsonify(error=e.text or "Discord rejected the name."),400
    return jsonify(ok=True,emoji=emoji_json(e))

@app.delete("/api/guild/<int:gid>/emoji/<int:emoji_id>")
@protected
def emoji_delete(gid,emoji_id):
    emoji=discord.utils.get(guild(gid).emojis,id=emoji_id)
    if not emoji:return jsonify(error="Emoji not found. It may already have been deleted."),404
    async def work():await emoji.delete(reason="Deleted from Jane Doe dashboard")
    try:bot.submit(work()).result(15)
    except discord.Forbidden:return jsonify(error="Give the bot Manage Expressions permission."),403
    except discord.HTTPException as e:return jsonify(error=e.text or "Discord rejected the emoji deletion."),400
    return jsonify(ok=True)

@app.post("/api/guild/<int:gid>/bot-profile")
@protected
def bot_profile(gid):
    g=guild(gid); nickname=request.form.get("nickname","").strip()[:32] or None; avatar_file=request.files.get("avatar"); avatar=None
    if avatar_file and avatar_file.filename:
        try:
            image=Image.open(io.BytesIO(avatar_file.read())).convert("RGBA"); image.thumbnail((512,512),Image.Resampling.LANCZOS); out=io.BytesIO(); image.save(out,"PNG",optimize=True); avatar=out.getvalue()
        except OSError:return jsonify(error="Use a valid PNG, JPG, GIF, or WEBP image."),400
    async def work():
        kwargs={"nick":nickname,"reason":"Server bot profile updated from dashboard"}
        if avatar is not None:kwargs["avatar"]=avatar
        return await g.me.edit(**kwargs)
    try:member=bot.submit(work()).result(20)
    except discord.Forbidden:return jsonify(error="The bot cannot update its server profile in this server."),403
    except discord.HTTPException as e:return jsonify(error=e.text or "Discord rejected the profile update."),400
    return jsonify(ok=True,name=(member or g.me).display_name,avatar=str((member or g.me).display_avatar.url))

@app.get("/api/guild/<int:gid>/bot-embeds")
@protected
def bot_embeds(gid):
    g=guild(gid)
    if not g:return jsonify(error="Bot is not connected to that server"),404
    rescan=request.args.get("rescan")=="1"
    async def work():
        if rescan:
            # Manual recovery only. Normal page loads use the persistent index below.
            limit=asyncio.Semaphore(2)
            async def scan(channel):
                try:
                    async with limit:
                        async for msg in channel.history(limit=100):
                            if msg.author.id==bot.user.id and msg.embeds:
                                item=embed_to_dict(msg.embeds[0]); storage.execute("INSERT OR REPLACE INTO bot_messages(message_id,guild_id,channel_id,content,embed_json,created_at) VALUES(?,?,?,?,?,?)",(msg.id,g.id,channel.id,msg.content,json.dumps(item),msg.created_at.isoformat()))
                                message_components(g.id,msg)
                except (discord.Forbidden,discord.HTTPException):pass
            await asyncio.gather(*(scan(c) for c in g.text_channels))
        saved=storage.rows("SELECT * FROM bot_messages WHERE guild_id=? ORDER BY created_at DESC",(g.id,)); result=[]
        for row in saved:
            channel=g.get_channel(row["channel_id"]); result.append({"channel_id":str(row["channel_id"]),"channel_name":channel.name if channel else str(row["channel_id"]),"message_id":str(row["message_id"]),"jump_url":f"https://discord.com/channels/{g.id}/{row['channel_id']}/{row['message_id']}","created_at":row["created_at"],"content":row["content"] or "","embed":json.loads(row["embed_json"] or "{}"),"components":saved_components(g.id,row["message_id"])})
        return result
    return jsonify(messages=bot.submit(work()).result(45))

def locate_message(g,raw_id,preferred_channel=None):
    match=re.search(r"channels/\d+/(\d+)/(\d+)",str(raw_id))
    channel_id=int(match.group(1)) if match else int(preferred_channel or 0)
    message_id=int(match.group(2)) if match else int(str(raw_id).strip())
    channel=g.get_channel(channel_id)
    if not channel:raise ValueError("Choose the message's channel, or paste its full Discord message link.")
    return channel,message_id

@app.post("/api/guild/<int:gid>/embed/load")
@protected
def load_embed(gid):
    d=request.get_json(force=True); g=guild(gid)
    try:channel,mid=locate_message(g,d.get("message_id"),d.get("channel_id"))
    except (ValueError,TypeError):return jsonify(error="Enter a valid message ID or full Discord message link."),400
    async def work():return await channel.fetch_message(mid)
    try:msg=bot.submit(work()).result(15)
    except discord.NotFound:return jsonify(error="Message not found in that channel. Paste the full message link to avoid selecting the wrong channel."),404
    except discord.Forbidden:return jsonify(error="The bot cannot read that channel or its message history."),403
    return jsonify(ok=True,channel_id=str(channel.id),message_id=str(msg.id),content=msg.content,embed=embed_to_dict(msg.embeds[0]) if msg.embeds else {},components=message_components(gid,msg))

@app.delete("/api/guild/<int:gid>/message")
@protected
def delete_message(gid):
    d=request.get_json(force=True); g=guild(gid)
    try:channel,mid=locate_message(g,d.get("message_id"),d.get("channel_id"))
    except (ValueError,TypeError):return jsonify(error="Invalid message or channel."),400
    async def work():
        msg=await channel.fetch_message(mid)
        if msg.author.id!=bot.user.id:raise PermissionError("Only messages sent by this bot can be deleted here.")
        await msg.delete()
    try:bot.submit(work()).result(15)
    except discord.NotFound:return jsonify(error="That message was already deleted."),404
    except discord.Forbidden:return jsonify(error="The bot cannot delete that message."),403
    except PermissionError as e:return jsonify(error=str(e)),400
    except discord.HTTPException as e:return jsonify(error=e.text or "Discord could not delete that message."),400
    except Exception as e:app.logger.exception("Message deletion failed"); return jsonify(error=f"Message deletion failed: {type(e).__name__}"),500
    old=storage.rows("SELECT component_key FROM message_component_configs WHERE message_id=? AND guild_id=?",(mid,gid)); storage.execute("DELETE FROM bot_messages WHERE message_id=?",(mid,)); storage.execute("DELETE FROM message_component_configs WHERE message_id=?",(mid,))
    if old:storage.execute("DELETE FROM settings WHERE guild_id=? AND key=?",(gid,f"message_components:{old[0]['component_key']}"))
    return jsonify(ok=True)

@app.post("/api/guild/<int:gid>/asset")
@protected
def upload_asset(gid):
    f=request.files.get("file")
    if not f or not f.filename:return jsonify(error="Choose an image first."),400
    ext=os.path.splitext(f.filename)[1].lower()
    if ext not in (".png",".jpg",".jpeg",".gif",".webp"):return jsonify(error="Use PNG, JPG, GIF or WEBP."),400
    token=f"{uuid.uuid4().hex}{ext}"; f.save(os.path.join(UPLOAD_DIR,token)); return jsonify(ok=True,asset_token=token,name=os.path.basename(f.filename))

@app.get("/api/guild/<int:gid>/asset/<token>")
@protected
def saved_asset(gid,token):return send_from_directory(UPLOAD_DIR,os.path.basename(token),conditional=True,max_age=3600)

@app.delete("/api/guild/<int:gid>/asset/<token>")
@protected
def delete_saved_asset(gid,token):
    path=os.path.join(UPLOAD_DIR,os.path.basename(token))
    try:
        if os.path.isfile(path):os.remove(path)
    except OSError as e:return jsonify(error=f"Could not delete saved file: {e}"),400
    return jsonify(ok=True)

@app.route("/api/guild/<int:gid>/settings/<key>",methods=["GET","PUT"])
@protected
def settings(gid,key):
    if request.method=="GET":return jsonify(storage.get_setting(gid,key,{}))
    incoming=request.get_json(force=True)
    # Settings editors can enhance the same card independently (for example,
    # welcome text and invite tracking). Merge those writes so a later save
    # cannot erase options owned by the other editor.
    current=storage.get_setting(gid,key,{})
    if isinstance(current,dict) and isinstance(incoming,dict):
        current.update(incoming); incoming=current
    storage.set_setting(gid,key,incoming); return jsonify(ok=True)

@app.route("/api/guild/<int:gid>/archives",methods=["GET","POST"])
@protected
def archives(gid):
    if request.method=="GET":
        items=[]
        for r in storage.rows("SELECT * FROM archives WHERE guild_id=? ORDER BY updated_at DESC",(gid,)):
            r["id"]=str(r["id"]); r["payload"]=json.loads(r.pop("payload_json")); items.append(r)
        return jsonify(archives=items)
    d=request.get_json(force=True); name=(d.get("name") or "Untitled embed").strip()[:100]
    archive_id=storage.execute("INSERT INTO archives(guild_id,name,payload_json) VALUES(?,?,?)",(gid,name,json.dumps(d.get("payload") or {})))
    return jsonify(ok=True,id=str(archive_id))

@app.route("/api/guild/<int:gid>/archives/<int:archive_id>",methods=["PUT","DELETE"])
@protected
def archive_item(gid,archive_id):
    if request.method=="DELETE":storage.execute("DELETE FROM archives WHERE id=? AND guild_id=?",(archive_id,gid)); return jsonify(ok=True)
    d=request.get_json(force=True); storage.execute("UPDATE archives SET name=?,payload_json=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND guild_id=?",((d.get("name") or "Untitled embed")[:100],json.dumps(d.get("payload") or {}),archive_id,gid)); return jsonify(ok=True)

def birthday_item(g,row):
    member=g.get_member(row["user_id"])
    if not member:
        async def fetch_member():
            try:return await g.fetch_member(row["user_id"])
            except (discord.NotFound,discord.Forbidden,discord.HTTPException):return None
        member=bot.submit(fetch_member()).result(12)
    today=datetime.now(timezone.utc).date(); year=today.year
    while True:
        try:next_date=datetime(year,row["month"],row["day"],tzinfo=timezone.utc).date()
        except ValueError:year+=1; continue
        if next_date<today:year+=1; continue
        break
    return {**row,"user_id":str(row["user_id"]),"name":member.display_name if member else f"User {row['user_id']}","avatar":str(member.display_avatar.url) if member else "","next_date":next_date.isoformat(),"days_until":(next_date-today).days}

@app.route("/api/guild/<int:gid>/birthdays",methods=["GET","POST","DELETE"])
@protected
def manage_birthdays(gid):
    g=guild(gid)
    if request.method=="GET":return jsonify(birthdays=sorted((birthday_item(g,r) for r in storage.rows("SELECT * FROM birthdays WHERE guild_id=?",(gid,))),key=lambda x:(x["days_until"],x["name"].lower())))
    d=request.get_json(force=True); match=re.search(r"\d{15,22}",str(d.get("user_id","")))
    if not match:return jsonify(error="Enter a valid Discord user ID or mention."),400
    uid=int(match.group());
    if request.method=="DELETE":storage.execute("DELETE FROM birthdays WHERE guild_id=? AND user_id=?",(gid,uid)); return jsonify(ok=True)
    try:month=int(d["month"]); day=int(d["day"]); year=int(d["year"]) if str(d.get("year","")).strip() else None; datetime(year or 2000,month,day)
    except (ValueError,TypeError,KeyError):return jsonify(error="Enter a valid birthday."),400
    storage.execute("INSERT INTO birthdays(guild_id,user_id,month,day,year) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET month=excluded.month,day=excluded.day,year=excluded.year",(gid,uid,month,day,year)); return jsonify(ok=True)

COMMAND_SECTIONS={
    "birthday":[("Announcements","announcements"),("Other","other")],
    "confess":[("Confessions & GIFs","social")],"hug":[("Confessions & GIFs","social")],"kiss":[("Confessions & GIFs","social")],"slap":[("Confessions & GIFs","social")],"pat":[("Confessions & GIFs","social")],"cuddle":[("Confessions & GIFs","social")],"bite":[("Confessions & GIFs","social")],
    "event":[("Polls & events","community")],"poll":[("Polls & events","community")],"purge":[("Other","other")],"stealemoji":[("Server emojis","emojis")],
    "warn":[("Commands","emojis")],"timeout":[("Commands","emojis")],"kick":[("Commands","emojis")],"ban":[("Commands","emojis")]
}

@app.get("/api/guild/<int:gid>/commands")
@protected
def commands_list(gid):
    disabled=set(storage.get_setting(0,"disabled_commands",[])); items=[]
    for command in sorted(bot.tree.get_commands(),key=lambda c:c.name):
        if command.name in disabled:continue
        items.append({"name":command.name,"description":command.description or "No description","sections":[{"label":label,"page":page} for label,page in COMMAND_SECTIONS.get(command.name,[("Overview","overview")])]})
    return jsonify(commands=items)

@app.delete("/api/guild/<int:gid>/commands/<name>")
@protected
def command_delete(gid,name):
    command=bot.tree.get_command(name)
    if not command:return jsonify(error="That command is already deleted."),404
    disabled=set(storage.get_setting(0,"disabled_commands",[])); disabled.add(name); storage.set_setting(0,"disabled_commands",sorted(disabled)); bot.tree.remove_command(name)
    async def work():await bot.tree.sync()
    try:bot.submit(work()).result(30)
    except discord.HTTPException as e:return jsonify(error=e.text or "Discord could not update the command list."),400
    return jsonify(ok=True)

@app.post("/api/guild/<int:gid>/purge/preview")
@protected
def purge_preview(gid):
    d=request.get_json(force=True); g=guild(gid); channel=g.get_channel(int(d.get("channel_id") or 0)); amount=max(1,min(int(d.get("amount") or 1),100))
    if not channel:return jsonify(error="Choose a text channel."),400
    async def work():
        items=[]
        async for msg in channel.history(limit=amount):items.append({"id":str(msg.id),"author":msg.author.display_name,"avatar":str(msg.author.display_avatar.url),"content":msg.content or (f"[{len(msg.attachments)} attachment(s)]" if msg.attachments else "[embed or system message]"),"created_at":msg.created_at.isoformat(),"pinned":msg.pinned})
        return items
    try:return jsonify(messages=bot.submit(work()).result(20))
    except discord.Forbidden:return jsonify(error="The bot cannot read that channel's history."),403

@app.post("/api/guild/<int:gid>/purge/delete")
@protected
def purge_delete(gid):
    d=request.get_json(force=True); g=guild(gid); channel=g.get_channel(int(d.get("channel_id") or 0)); ids=[int(x) for x in (d.get("message_ids") or [])[:100]]
    if not channel or not ids:return jsonify(error="Preview messages before deleting."),400
    async def work():
        deleted=0
        for mid in ids:
            try:await channel.get_partial_message(mid).delete(); deleted+=1
            except discord.NotFound:pass
        return deleted
    try:return jsonify(ok=True,deleted=bot.submit(work()).result(90))
    except discord.Forbidden:return jsonify(error="The bot needs Manage Messages in that channel."),403
    except discord.HTTPException as e:return jsonify(error=e.text or "Discord could not delete those messages."),400
    except Exception as e:app.logger.exception("Purge deletion failed"); return jsonify(error=f"Purge deletion failed: {type(e).__name__}"),500

@app.post("/api/guild/<int:gid>/embed/send")
@protected
def send_embed(gid):
    d=request.get_json(force=True); g=guild(gid); channel=g.get_channel(int(d.get("channel_id") or 0))
    if not isinstance(channel,(discord.TextChannel,discord.Thread)) or not channel.permissions_for(g.me).send_messages:return jsonify(error="Jane Doe cannot post there. Choose a text channel and grant View Channel + Send Messages."),403
    async def work():
        files=[]; edata=d.get("embed") or {}; token=d.get("asset_token"); thumb_token=d.get("thumbnail_asset_token"); author_token=d.get("author_icon_asset_token"); component_key=None
        if token:
            path=os.path.join(UPLOAD_DIR,os.path.basename(token)); files.append(discord.File(path,filename=os.path.basename(path))); edata={**edata,"image":f"attachment://{os.path.basename(path)}"}
        if thumb_token:
            path=os.path.join(UPLOAD_DIR,os.path.basename(thumb_token)); files.append(discord.File(path,filename=os.path.basename(path))); edata={**edata,"thumbnail":f"attachment://{os.path.basename(path)}"}
        if author_token:
            path=os.path.join(UPLOAD_DIR,os.path.basename(author_token)); files.append(discord.File(path,filename=os.path.basename(path))); edata={**edata,"author_icon":f"attachment://{os.path.basename(path)}"}
        view=None
        if d.get("buttons") or d.get("menus"):
            component_key=secrets.token_hex(6); components={"buttons":d.get("buttons") or [],"menus":d.get("menus") or []}; storage.set_setting(gid,f"message_components:{component_key}",components); view=ActionButtonView(component_key,components)
            if view.is_persistent():bot.add_view(view)
        msg=await channel.send(content=d.get("content") or None,embed=make_embed(edata) if has_embed_content(edata) else None,files=files,view=view)
        if component_key:storage.execute("INSERT OR REPLACE INTO message_component_configs VALUES(?,?,?)",(msg.id,gid,component_key))
        return str(msg.id)
    return jsonify(ok=True,message_id=bot.submit(work()).result(15))

@app.post("/api/guild/<int:gid>/embed/edit")
@protected
def edit_embed(gid):
    d=request.get_json(force=True); g=guild(gid)
    try:channel,mid=locate_message(g,d.get("message_id"),d.get("channel_id"))
    except (ValueError,TypeError):return jsonify(error="Enter a valid message ID or full message link."),400
    async def work():
        msg=await channel.fetch_message(mid)
        if msg.author.id!=bot.user.id:raise PermissionError("Discord only allows the bot to edit messages it originally sent.")
        edata=d.get("embed") or {}; kwargs={"content":d.get("content") or None,"embed":make_embed(edata) if has_embed_content(edata) else None}
        token=d.get("asset_token")
        if token:
            path=os.path.join(UPLOAD_DIR,os.path.basename(token)); kwargs["attachments"]=[discord.File(path,filename=os.path.basename(path))]; edata={**edata,"image":f"attachment://{os.path.basename(path)}"}; kwargs["embed"]=make_embed(edata)
        thumb_token=d.get("thumbnail_asset_token")
        if thumb_token:
            path=os.path.join(UPLOAD_DIR,os.path.basename(thumb_token)); kwargs.setdefault("attachments",[]).append(discord.File(path,filename=os.path.basename(path))); edata={**edata,"thumbnail":f"attachment://{os.path.basename(path)}"}; kwargs["embed"]=make_embed(edata)
        author_token=d.get("author_icon_asset_token")
        if author_token:
            path=os.path.join(UPLOAD_DIR,os.path.basename(author_token)); kwargs.setdefault("attachments",[]).append(discord.File(path,filename=os.path.basename(path))); edata={**edata,"author_icon":f"attachment://{os.path.basename(path)}"}; kwargs["embed"]=make_embed(edata)
        old=storage.rows("SELECT component_key FROM message_component_configs WHERE message_id=? AND guild_id=?",(mid,gid))
        if d.get("buttons") or d.get("menus"):
            key=secrets.token_hex(6); components={"buttons":d.get("buttons") or [],"menus":d.get("menus") or []}; storage.set_setting(gid,f"message_components:{key}",components); kwargs["view"]=ActionButtonView(key,components)
            if kwargs["view"].is_persistent():bot.add_view(kwargs["view"])
            storage.execute("INSERT OR REPLACE INTO message_component_configs VALUES(?,?,?)",(mid,gid,key))
        elif d.get("replace_components"):kwargs["view"]=None; storage.execute("DELETE FROM message_component_configs WHERE message_id=?",(mid,))
        if old and (d.get("buttons") or d.get("menus") or d.get("replace_components")):storage.execute("DELETE FROM settings WHERE guild_id=? AND key=?",(gid,f"message_components:{old[0]['component_key']}"))
        await msg.edit(**kwargs); return True
    try:bot.submit(work()).result(15)
    except discord.NotFound:return jsonify(error="Message not found. Choose its channel or paste the full Discord message link."),404
    except discord.Forbidden:return jsonify(error="The bot cannot access or edit that message."),403
    except PermissionError as e:return jsonify(error=str(e)),400
    except discord.HTTPException as e:return jsonify(error=f"Discord rejected the edit: {e.text or 'check the embed fields.'}"),400
    return jsonify(ok=True)

@app.post("/api/guild/<int:gid>/ticket-panel")
@protected
def ticket_panel(gid):
    d=request.get_json(force=True); key=d.get("key") or secrets.token_hex(4); storage.set_setting(gid,f"ticket_panel:{key}",d); channel=guild(gid).get_channel(int(d["channel_id"]))
    async def work():
        view=TicketPanel(key,d)
        bot.add_view(view)
        embed,files=make_embed_with_files(d.get("embed")); msg=await channel.send(content=d.get("content") or None,embed=embed,files=files,view=view); return str(msg.id)
    try:return jsonify(ok=True,key=key,message_id=bot.submit(work()).result(15))
    except discord.HTTPException as e:return jsonify(error=f"Discord rejected the ticket panel: {e.text or str(e)}"),400

@app.post("/api/guild/<int:gid>/reaction-role")
@protected
def reaction_role(gid):
    d=request.get_json(force=True); channel=guild(gid).get_channel(int(d["channel_id"])); emoji=d["emoji"]
    async def work():
        msg=await channel.send(embed=make_embed(d.get("embed"))); await msg.add_reaction(emoji); return msg.id
    mid=bot.submit(work()).result(15); storage.execute("INSERT OR REPLACE INTO reaction_roles VALUES(?,?,?,?,?)",(gid,mid,int(d['channel_id']),emoji,int(d['role_id']))); return jsonify(ok=True,message_id=str(mid))

@app.post("/api/guild/<int:gid>/reaction-panel")
@protected
def reaction_panel(gid):
    d=request.get_json(force=True); choices=[c for c in d.get("choices",[]) if c.get("label") and c.get("role_id")][:25]
    if not choices:return jsonify(error="Add at least one role choice."),400
    d["choices"]=choices; key=d.get("key") or secrets.token_hex(5); storage.set_setting(gid,f"reaction_panel:{key}",d); channel=guild(gid).get_channel(int(d["channel_id"]))
    async def work():
        view=ReactionRoleView(key,d)
        bot.add_view(view); embed,files=make_embed_with_files(d.get("embed")); msg=await channel.send(content=d.get("content") or None,embed=embed,files=files,view=view); return msg.id
    return jsonify(ok=True,key=key,message_id=str(bot.submit(work()).result(15)))

@app.post("/api/guild/<int:gid>/glue")
@protected
def glue(gid):
    d=request.get_json(force=True); channel_id=int(d['channel_id']); storage.execute("INSERT INTO glue(guild_id,channel_id,content,embed_json,enabled) VALUES(?,?,?,?,1) ON CONFLICT(channel_id) DO UPDATE SET content=excluded.content,embed_json=excluded.embed_json,enabled=1",(gid,channel_id,d.get('content'),json.dumps(d.get('embed')) if d.get('embed') else None)); storage.set_setting(gid,f"glue_options:{channel_id}",{"template_enabled":d.get("template_enabled",False),"template":d.get("template", ""),"button_label":d.get("button_label","Show template"),"button_emoji":d.get("button_emoji","")}); return jsonify(ok=True)

@app.get("/api/guild/<int:gid>/glue")
@protected
def get_glue(gid):
    rows=storage.rows("SELECT * FROM glue WHERE guild_id=? AND enabled=1 ORDER BY channel_id",(gid,)); g=guild(gid); items=[]
    for row in rows:
        channel=g.get_channel(row['channel_id']); options=storage.get_setting(gid,f"glue_options:{row['channel_id']}",{}); items.append({"channel_id":str(row['channel_id']),"channel_name":channel.name if channel else str(row['channel_id']),"content":row.get('content') or '',"embed":json.loads(row['embed_json']) if row.get('embed_json') else {},**options})
    return jsonify(enabled=bool(items),items=items)

@app.delete("/api/guild/<int:gid>/glue/<int:channel_id>")
@protected
def delete_glue(gid,channel_id):
    storage.execute("UPDATE glue SET enabled=0 WHERE guild_id=? AND channel_id=?",(gid,channel_id)); return jsonify(ok=True)

@app.post("/api/guild/<int:gid>/giveaway")
@protected
def giveaway(gid):
    d=request.get_json(force=True); channel=guild(gid).get_channel(int(d['channel_id'])); end=datetime.now(timezone.utc)+timedelta(minutes=int(d['minutes'])); entry_emoji=d.get("entry_emoji") or "🎉"
    async def work():
        edata={**(d.get("embed") or {}),"title":(d.get("embed") or {}).get("title") or "GIVEAWAY","description":f"**{d['prize']}**\n\nReact with {entry_emoji} to enter!\nEnds <t:{int(end.timestamp())}:R>"}; embed,files=make_embed_with_files(edata); msg=await channel.send(embed=embed,files=files); await msg.add_reaction(entry_emoji); return msg.id
    mid=bot.submit(work()).result(15); storage.execute("INSERT INTO giveaways VALUES(?,?,?,?,?,?,0)",(mid,gid,int(d['channel_id']),d['prize'],int(d.get('winners',1)),end.isoformat())); storage.set_setting(gid,f"giveaway_emoji:{mid}",entry_emoji); return jsonify(ok=True,message_id=str(mid))

@app.post("/api/guild/<int:gid>/poll")
@protected
def create_poll(gid):
    d=request.get_json(force=True); channel=guild(gid).get_channel(int(d["channel_id"])); options=[x.strip() for x in d.get("options",[]) if x.strip()][:10]
    if len(options)<2:return jsonify(error="Add at least two poll options."),400
    key=secrets.token_hex(6); storage.set_setting(gid,f"poll:{key}",{"options":options,"question":d["question"]})
    async def work():
        view=PollView(key,options); bot.add_view(view)
        edata={**(d.get("embed") or {}),"title":d["question"],"description":(d.get("embed") or {}).get("description") or "Choose an option below."}; embed,files=make_embed_with_files(edata); msg=await channel.send(embed=embed,files=files,view=view)
        return msg.id
    return jsonify(ok=True,message_id=str(bot.submit(work()).result(15)))

@app.post("/api/guild/<int:gid>/event")
@protected
def create_event(gid):
    d=request.get_json(force=True); g=guild(gid); start=datetime.now(timezone.utc)+timedelta(minutes=int(d["starts_in_minutes"])); end=start+timedelta(minutes=int(d["duration_minutes"]))
    async def work():
        event=await g.create_scheduled_event(name=d["name"],description=d.get("description") or None,start_time=start,end_time=end,entity_type=discord.EntityType.external,privacy_level=discord.PrivacyLevel.guild_only,location=d["location"],reason="Created from Jane Doe dashboard")
        buttons=d.get("buttons") or []; channel=g.get_channel(int(d.get("channel_id") or 0))
        if buttons and channel:
            for b in buttons:
                if b.get("action")=="link" and not b.get("url"):b["url"]=str(event.url)
            key=secrets.token_hex(6); storage.set_setting(gid,f"message_components:{key}",{"buttons":buttons}); view=ActionButtonView(key,buttons); edata={**(d.get("embed") or {}),"title":event.name,"description":d.get("description") or f"Starts <t:{int(start.timestamp())}:R>","color":"#5865F2"}; embed,files=make_embed_with_files(edata); await channel.send(embed=embed,files=files,view=view)
        return event.id
    return jsonify(ok=True,event_id=str(bot.submit(work()).result(15)))

def run_dashboard():
    from waitress import serve
    serve(app,host=config.HOST,port=config.PORT,threads=8,channel_timeout=90)
