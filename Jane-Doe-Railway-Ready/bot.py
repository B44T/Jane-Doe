import asyncio, io, json, random, re, os, sys, secrets
from datetime import datetime, timezone, timedelta
import discord, requests
from discord import app_commands
from discord.ext import commands, tasks
from zoneinfo import ZoneInfo
import config, storage
from embed_utils import make_embed, variables, has_embed_content, make_embed_with_files, embed_to_dict

intents=discord.Intents.default(); intents.members=True; intents.message_content=True; intents.reactions=True
member_cache=discord.MemberCacheFlags.all()
bot=commands.Bot(command_prefix="!",intents=intents,application_id=config.APPLICATION_ID,member_cache_flags=member_cache,max_messages=3000)
suppressed_delete_logs=set()

async def resolve_member(guild,user_id):
    member=guild.get_member(user_id)
    if member:return member
    try:return await guild.fetch_member(user_id)
    except (discord.NotFound,discord.Forbidden,discord.HTTPException):return None

def staff(interaction): return interaction.user.guild_permissions.manage_guild
def publisher(interaction):
    permissions=interaction.user.guild_permissions
    return permissions.manage_messages or permissions.manage_guild
def event_manager(interaction):
    permissions=interaction.user.guild_permissions
    return permissions.manage_events or permissions.manage_guild
def component_emoji(value):
    if not value:return None
    try:return discord.PartialEmoji.from_str(str(value))
    except (TypeError,ValueError):return None

def ordinal(number):
    suffix="th" if 10<=number%100<=20 else {1:"st",2:"nd",3:"rd"}.get(number%10,"th")
    return f"{number}{suffix}"

def age_text(delta):
    seconds=max(0,int(delta.total_seconds())); units=(("year",31536000),("month",2592000),("day",86400),("hour",3600),("minute",60),("second",1)); parts=[]
    for name,size in units:
        value,seconds=divmod(seconds,size)
        if value:parts.append(f"{value} {name}{'' if value==1 else 's'}")
        if len(parts)==3:break
    return ", ".join(parts) or "0 seconds"

def clipped(value,limit=1024):
    value=str(value or "")
    return value if len(value)<=limit else value[:limit-1]+"…"

def announcement_embed(cfg,member=None):
    edata=cfg.get("embed") or {}
    if member:edata={k:variables(v,member) if isinstance(v,str) else v for k,v in edata.items()}
    else:edata=dict(edata)
    if cfg.get("anonymous"):
        edata.pop("author",None); edata.pop("author_icon",None); edata.pop("author_icon_asset",None)
    return edata

async def action_log(guild,event,member,title,description="",fields=None,color=0xB8343F):
    cfg=storage.get_setting(guild.id,"action_logs",{})
    if not cfg.get("enabled") or not cfg.get(event):return
    channel=guild.get_channel(int(cfg.get("channel_id") or 0))
    if not channel:return
    embed=discord.Embed(title=title,description=clipped(description,4096) or None,color=color,timestamp=datetime.now(timezone.utc))
    if member:
        display=getattr(member,"display_name",None) or getattr(member,"name","Unknown user"); username=getattr(member,"name","unknown")
        embed.set_author(name=f"{display} · @{username}",icon_url=str(member.display_avatar.url))
    for name,value,inline in fields or []:embed.add_field(name=name,value=clipped(value) or "None",inline=inline)
    try:await channel.send(embed=embed)
    except (discord.Forbidden,discord.HTTPException):pass

@bot.tree.error
async def command_error(interaction,error):
    if isinstance(error,app_commands.CommandOnCooldown):text=f"Please wait {error.retry_after:.0f} seconds before using that again."
    elif isinstance(error,app_commands.CheckFailure):text="You do not have permission to use that command."
    else:text="That command could not be completed. Check the bot's permissions and try again."
    try:
        if interaction.response.is_done():await interaction.followup.send(text,ephemeral=True)
        else:await interaction.response.send_message(text,ephemeral=True)
    except discord.HTTPException:pass

class TicketPanel(discord.ui.View):
    def __init__(self,key="default",cfg=None):
        super().__init__(timeout=None); self.key=key; self.cfg=cfg or {}
        options=self.cfg.get("options") or []
        if self.cfg.get("mode")=="select" and options:
            select=discord.ui.Select(placeholder=self.cfg.get("placeholder") or "Choose a ticket type",custom_id=f"ticket:select:{key}",min_values=1,max_values=1,options=[discord.SelectOption(label=o.get("label","Ticket")[:100],value=str(i),description=(o.get("description") or None),emoji=component_emoji(o.get("emoji"))) for i,o in enumerate(options[:25])])
            async def selected(interaction):await self.open_ticket(interaction,int(select.values[0]))
            select.callback=selected; self.add_item(select)
        else:
            button=discord.ui.Button(label=self.cfg.get("button_label","Open ticket"),emoji=component_emoji(self.cfg.get("button_emoji")),style=discord.ButtonStyle.primary,custom_id=f"ticket:open:{key}")
            async def clicked(interaction):await self.open_ticket(interaction,0)
            button.callback=clicked; self.add_item(button)
    async def open_ticket(self,interaction,option_index=0):
        if not interaction.response.is_done():await interaction.response.defer(ephemeral=True)
        guild=interaction.guild; user=interaction.user
        old=storage.rows("SELECT channel_id FROM tickets WHERE guild_id=? AND owner_id=? AND status='open'",(guild.id,user.id))
        if old:return await interaction.followup.send(f"You already have <#{old[0]['channel_id']}> open.",ephemeral=True)
        cfg=storage.get_setting(guild.id,f"ticket_panel:{self.key}",{}); options=cfg.get("options") or []; option=options[option_index] if option_index<len(options) else {}
        category=guild.get_channel(int(option.get("category_id") or cfg.get("category_id") or 0)); staff_role=guild.get_role(int(option.get("staff_role_id") or cfg.get("staff_role_id") or 0))
        overwrites={guild.default_role:discord.PermissionOverwrite(view_channel=False),user:discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True),guild.me:discord.PermissionOverwrite(view_channel=True,manage_channels=True)}
        if staff_role: overwrites[staff_role]=discord.PermissionOverwrite(view_channel=True,send_messages=True,read_message_history=True)
        prefix=re.sub(r"[^a-z0-9-]","-",(option.get("channel_prefix") or "ticket").lower())
        channel=await guild.create_text_channel(f"{prefix}-{user.name}"[:90],category=category,overwrites=overwrites,topic=f"Ticket owner: {user.id} · Type: {option.get('label','General')}")
        storage.execute("INSERT INTO tickets(guild_id,channel_id,owner_id,panel_key) VALUES(?,?,?,?)",(guild.id,channel.id,user.id,self.key))
        intro=" ".join(x for x in (user.mention,staff_role.mention if staff_role else "",option.get("open_content") or cfg.get("open_content", "")) if x)
        edata=dict(option.get("open_embed") or cfg.get("open_embed") or {"title":"Support ticket","description":"Tell us what you need help with. A team member will be with you soon."}); edata["fields"]=[{"name":"Ticket type","value":option.get("label") or "General","inline":False}]+list(edata.get("fields") or [])
        embed,files=make_embed_with_files(edata); await channel.send(content=intro,embed=embed,files=files,view=TicketControls())
        await interaction.followup.send(f"Created {channel.mention}",ephemeral=True)

class TicketControls(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label="Claim",style=discord.ButtonStyle.secondary,custom_id="ticket:claim")
    async def claim(self,interaction,button):
        if not interaction.user.guild_permissions.manage_messages:return await interaction.response.send_message("Staff only.",ephemeral=True)
        await interaction.response.send_message(f"Claimed by {interaction.user.mention}")
    @discord.ui.button(label="Close",style=discord.ButtonStyle.danger,custom_id="ticket:close")
    async def close(self,interaction,button):
        row=storage.rows("SELECT * FROM tickets WHERE channel_id=? AND status='open'",(interaction.channel_id,))
        if not row:return await interaction.response.send_message("This is not an open ticket.",ephemeral=True)
        if interaction.user.id!=row[0]["owner_id"] and not interaction.user.guild_permissions.manage_channels:return await interaction.response.send_message("You cannot close this ticket.",ephemeral=True)
        await interaction.response.send_message("Closing in 5 seconds…"); storage.execute("UPDATE tickets SET status='closed' WHERE channel_id=?",(interaction.channel_id,)); await asyncio.sleep(5); await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")

class PollView(discord.ui.View):
    def __init__(self,key,options):
        super().__init__(timeout=None); self.key=key
        for index,label in enumerate(options[:5]):
            match=re.search(r"<(a?):([A-Za-z0-9_]+):(\d+)>",label); emoji=component_emoji(match.group(0)) if match else None; clean=(label.replace(match.group(0),"").strip() if match else label) or "Option"
            button=discord.ui.Button(label=clean[:80],emoji=emoji,style=discord.ButtonStyle.secondary,custom_id=f"poll:{key}:{index}")
            async def vote(interaction,idx=index):
                storage.execute("INSERT INTO poll_votes(guild_id,poll_key,user_id,option_index) VALUES(?,?,?,?) ON CONFLICT(guild_id,poll_key,user_id) DO UPDATE SET option_index=excluded.option_index",(interaction.guild_id,self.key,interaction.user.id,idx))
                await interaction.response.send_message(f"Vote saved: **{options[idx]}**",ephemeral=True)
            button.callback=vote; self.add_item(button)

class ReactionRoleView(discord.ui.View):
    def __init__(self,key,cfg):
        super().__init__(timeout=None); self.key=key; choices=(cfg.get("choices") or [])[:25]
        if cfg.get("mode")=="select":
            select=discord.ui.Select(placeholder=cfg.get("placeholder") or "Choose your roles",custom_id=f"roles:select:{key}",min_values=0,max_values=max(1,min(len(choices),25)),options=[discord.SelectOption(label=c.get("label","Role")[:100],value=str(i),description=c.get("description") or None,emoji=component_emoji(c.get("emoji"))) for i,c in enumerate(choices)])
            async def selected(interaction):
                await interaction.response.defer(ephemeral=True)
                selected={int(x) for x in select.values}; roles=[interaction.guild.get_role(int(c.get("role_id") or 0)) for c in choices]
                for i,role in enumerate(roles):
                    if not role:continue
                    if i in selected and role not in interaction.user.roles:await interaction.user.add_roles(role,reason="Role menu")
                    elif i not in selected and role in interaction.user.roles:await interaction.user.remove_roles(role,reason="Role menu")
                await interaction.followup.send("Your roles were updated.",ephemeral=True)
            select.callback=selected; self.add_item(select)
        else:
            for i,c in enumerate(choices[:25]):
                button=discord.ui.Button(label=c.get("label","Role")[:80],emoji=component_emoji(c.get("emoji")),style=discord.ButtonStyle.secondary,custom_id=f"roles:{key}:{i}",row=i//5)
                async def toggle(interaction,choice=c):
                    role=interaction.guild.get_role(int(choice.get("role_id") or 0))
                    if not role:return await interaction.response.send_message("That role no longer exists.",ephemeral=True)
                    await interaction.response.defer(ephemeral=True); added=role not in interaction.user.roles; await (interaction.user.add_roles(role,reason="Role button") if added else interaction.user.remove_roles(role,reason="Role button")); await interaction.followup.send(f"{role.mention} {'added' if added else 'removed'}.",ephemeral=True)
                button.callback=toggle; self.add_item(button)

class ActionButtonView(discord.ui.View):
    def __init__(self,key,components):
        super().__init__(timeout=None); self.key=key
        if isinstance(components,list):components={"buttons":components}
        components=components or {}; buttons=components.get("buttons") or []; menus=components.get("menus") or []; button_limit=max(0,(5-min(len(menus),5))*5); visible_buttons=buttons[:button_limit]
        styles={"primary":discord.ButtonStyle.primary,"secondary":discord.ButtonStyle.secondary,"success":discord.ButtonStyle.success,"danger":discord.ButtonStyle.danger}
        for i,c in enumerate(visible_buttons):
            emoji=component_emoji(c.get("emoji"))
            if c.get("action")=="link" and c.get("url"):
                self.add_item(discord.ui.Button(label=c.get("label","Open")[:80],emoji=emoji,style=discord.ButtonStyle.link,url=c["url"],row=i//5)); continue
            button=discord.ui.Button(label=c.get("label","Button")[:80],emoji=emoji,style=styles.get(c.get("style"),discord.ButtonStyle.secondary),custom_id=f"action:{key}:{i}",row=i//5)
            async def clicked(interaction,choice=c):
                if choice.get("action")=="confession_submit":return await interaction.response.send_modal(ConfessionSubmitModal())
                if choice.get("action")=="confession_reply":return await interaction.response.send_modal(ConfessionReplyModal(int(choice.get("confession_id") or 0)))
                if choice.get("action")=="role":
                    role=interaction.guild.get_role(int(choice.get("role_id") or 0))
                    if not role:return await interaction.response.send_message("That role no longer exists.",ephemeral=True)
                    await interaction.response.defer(ephemeral=True); added=role not in interaction.user.roles; await (interaction.user.add_roles(role,reason="Message button") if added else interaction.user.remove_roles(role,reason="Message button")); return await interaction.followup.send(f"{role.mention} {'added' if added else 'removed'}.",ephemeral=True)
                await interaction.response.send_message(choice.get("response") or "Button clicked.",ephemeral=choice.get("ephemeral",True))
            button.callback=clicked; self.add_item(button)
        remaining_rows=max(0,5-((len(visible_buttons)+4)//5))
        for menu_index,menu in enumerate(menus[:remaining_rows]):
            options=(menu.get("options") or [])[:25]
            if not options:continue
            select=discord.ui.Select(placeholder=(menu.get("placeholder") or "Choose an option")[:150],custom_id=f"actionmenu:{key}:{menu_index}",min_values=1,max_values=1,options=[discord.SelectOption(label=(o.get("label") or "Option")[:100],value=str(i),description=(o.get("description") or None),emoji=component_emoji(o.get("emoji"))) for i,o in enumerate(options)])
            async def selected(interaction,select=select,choices=options):
                choice=choices[int(select.values[0])]
                await interaction.response.send_message(choice.get("response") or "No response has been configured.",ephemeral=choice.get("ephemeral",True))
            select.callback=selected; self.add_item(select)

class GlueTemplateView(discord.ui.View):
    def __init__(self,channel_id,cfg):
        super().__init__(timeout=None); self.channel_id=channel_id
        emoji=component_emoji(cfg.get("button_emoji"))
        button=discord.ui.Button(label=cfg.get("button_label") or "Show template",emoji=emoji,style=discord.ButtonStyle.secondary,custom_id=f"glue:template:{channel_id}")
        async def show(interaction):await interaction.response.send_message(cfg.get("template") or "No template has been saved.",ephemeral=True)
        button.callback=show; self.add_item(button)

class ConfessionSubmitModal(discord.ui.Modal,title="Submit an anonymous confession"):
    confession=discord.ui.TextInput(label="Confession",style=discord.TextStyle.paragraph,max_length=1800,required=True,placeholder="Your identity will not be shown.")
    async def on_submit(self,interaction):await post_confession(interaction,str(self.confession))

class ConfessionReplyModal(discord.ui.Modal,title="Reply anonymously"):
    reply=discord.ui.TextInput(label="Reply",style=discord.TextStyle.paragraph,max_length=1800,required=True,placeholder="Your reply will be posted anonymously in the thread.")
    def __init__(self,confession_id):super().__init__(); self.confession_id=confession_id
    async def on_submit(self,interaction):await post_confession_reply(interaction,self.confession_id,str(self.reply))

class ConfessionView(discord.ui.View):
    def __init__(self,confession_id,cfg=None):
        super().__init__(timeout=None); self.confession_id=confession_id; cfg=cfg or {}
        submit=discord.ui.Button(label=(cfg.get("submit_label") or "Submit a confession")[:80],emoji=component_emoji(cfg.get("submit_emoji") or ""),style=discord.ButtonStyle.primary,custom_id="confession:submit")
        reply=discord.ui.Button(label=(cfg.get("reply_label") or "Reply anonymously")[:80],emoji=component_emoji(cfg.get("reply_emoji") or ""),style=discord.ButtonStyle.secondary,custom_id=f"confession:reply:{confession_id}")
        async def submit_clicked(interaction):await interaction.response.send_modal(ConfessionSubmitModal())
        async def reply_clicked(interaction):await interaction.response.send_modal(ConfessionReplyModal(confession_id))
        submit.callback=submit_clicked; reply.callback=reply_clicked; self.add_item(submit); self.add_item(reply)

async def get_confession_thread(guild,row,cfg):
    thread_id=int(row.get("thread_id") or row.get("message_id") or 0)
    thread=guild.get_thread(thread_id) if thread_id else None
    if not thread and thread_id:
        try:
            found=await bot.fetch_channel(thread_id)
            if isinstance(found,discord.Thread):thread=found
        except (discord.NotFound,discord.Forbidden,discord.HTTPException):pass
    if thread:return thread
    channel=guild.get_channel(int(row.get("channel_id") or cfg.get("channel_id") or 0))
    if not channel:raise RuntimeError("The confession channel no longer exists.")
    message=await channel.fetch_message(int(row.get("message_id") or 0))
    name=(cfg.get("thread_name") or "Confession #{id} replies").replace("{id}",str(row["id"]))[:100]
    thread=await message.create_thread(name=name,auto_archive_duration=1440,reason="Anonymous confession replies")
    storage.execute("UPDATE confessions SET thread_id=? WHERE id=?",(thread.id,row["id"])); return thread

async def publish_confession(guild,content,attachment_urls=None,attachment_payloads=None):
    cfg=storage.get_setting(guild.id,"confessions",{}); channel=guild.get_channel(int(cfg.get("channel_id") or 0))
    if not cfg.get("enabled") or not channel:raise RuntimeError("Confessions are not configured here.")
    attachment_urls=[str(x) for x in (attachment_urls or []) if x]; attachment_payloads=attachment_payloads or []; description=content.strip() if content else ""
    image_url=next((x for x in attachment_urls if re.search(r"\.(png|jpe?g|gif|webp)(\?|$)",x,re.I)),None)
    extra=[x for x in attachment_urls if x!=image_url]
    if extra:description+=("\n\n" if description else "")+"\n".join(f"[Attachment {i+1}]({url})" for i,url in enumerate(extra))
    if not description:description="Anonymous attachment"
    cid=storage.execute("INSERT INTO confessions(guild_id,user_id,content,channel_id) VALUES(?,?,?,?)",(guild.id,0,description,channel.id))
    base=cfg.get("embed") or {}; title=(base.get("title") or "Anonymous confession #{id}").replace("{id}",str(cid)); edata={**base,"title":title,"description":description}
    uploaded=[]
    for index,(raw,extension,content_type) in enumerate(attachment_payloads[:10],1):
        filename=f"confession-{cid}-{index}{extension}"; uploaded.append(discord.File(io.BytesIO(raw),filename=filename))
        if not image_url and str(content_type).startswith("image/"):image_url=f"attachment://{filename}"
    if image_url:edata["image"]=image_url
    key=f"confession-{cid}"; components={"buttons":[{"label":cfg.get("submit_label") or "Submit a confession","emoji":cfg.get("submit_emoji") or "","style":"primary","action":"confession_submit"},{"label":cfg.get("reply_label") or "Reply anonymously","emoji":cfg.get("reply_emoji") or "","style":"secondary","action":"confession_reply","confession_id":cid}]}; storage.set_setting(guild.id,f"message_components:{key}",components)
    embed,files=make_embed_with_files(edata); view=ActionButtonView(key,components); bot.add_view(view); msg=await channel.send(content=cfg.get("content") or None,embed=embed,files=files+uploaded,view=view)
    storage.execute("UPDATE confessions SET message_id=? WHERE id=?",(msg.id,cid)); storage.execute("INSERT OR REPLACE INTO message_component_configs VALUES(?,?,?)",(msg.id,guild.id,key)); storage.execute("INSERT OR REPLACE INTO bot_messages(message_id,guild_id,channel_id,content,embed_json,created_at) VALUES(?,?,?,?,?,?)",(msg.id,guild.id,channel.id,cfg.get("content") or "",json.dumps(embed_to_dict(embed)),msg.created_at.isoformat()))
    try:await get_confession_thread(guild,{"id":cid,"channel_id":channel.id,"message_id":msg.id,"thread_id":None},cfg)
    except (discord.Forbidden,discord.HTTPException):pass
    return cid

async def post_confession(interaction,content):
    cfg=storage.get_setting(interaction.guild_id,"confessions",{}); channel=interaction.guild.get_channel(int(cfg.get("channel_id") or 0))
    if not cfg.get("enabled") or not channel:return await interaction.response.send_message("Confessions are not configured here.",ephemeral=True)
    await interaction.response.defer(ephemeral=True,thinking=True)
    cid=await publish_confession(interaction.guild,content)
    await interaction.followup.send(f"Confession #{cid} was posted anonymously.",ephemeral=True)

async def post_confession_reply(interaction,confession_id,content):
    rows=storage.rows("SELECT * FROM confessions WHERE id=? AND guild_id=?",(confession_id,interaction.guild_id))
    if not rows:return await interaction.response.send_message("That confession no longer exists.",ephemeral=True)
    await interaction.response.defer(ephemeral=True,thinking=True)
    cfg=storage.get_setting(interaction.guild_id,"confessions",{})
    try:thread=await get_confession_thread(interaction.guild,rows[0],cfg)
    except discord.Forbidden:return await interaction.followup.send("I need Create Public Threads and Send Messages in Threads to post replies.",ephemeral=True)
    except (discord.NotFound,discord.HTTPException,RuntimeError) as e:return await interaction.followup.send(f"The reply thread could not be opened: {e}",ephemeral=True)
    reply_id=storage.execute("INSERT INTO confession_replies(confession_id,guild_id,user_id,content) VALUES(?,?,?,?)",(confession_id,interaction.guild_id,0,content))
    title=(cfg.get("reply_title") or "Anonymous reply #{reply_id}").replace("{reply_id}",str(reply_id)).replace("{id}",str(confession_id)); footer=(cfg.get("reply_footer") or "Reply to confession #{id}").replace("{id}",str(confession_id)).replace("{reply_id}",str(reply_id)); edata={"title":title,"description":content,"color":cfg.get("reply_color") or (cfg.get("embed") or {}).get("color") or "#2b2d31","footer":footer,"author":cfg.get("reply_author") or "","author_icon":cfg.get("reply_author_icon") or "","author_icon_asset":cfg.get("reply_author_icon_asset") or ""}
    try:
        if thread.archived:await thread.edit(archived=False,reason="New anonymous confession reply")
        embed,files=make_embed_with_files(edata); msg=await thread.send(embed=embed,files=files); storage.execute("UPDATE confession_replies SET message_id=? WHERE id=?",(msg.id,reply_id))
    except discord.Forbidden:return await interaction.followup.send("I cannot send messages in that confession thread.",ephemeral=True)
    await interaction.followup.send(f"Anonymous reply #{reply_id} was posted in the confession thread.",ephemeral=True)

@bot.event
async def on_ready():
    await bot.change_presence(status=discord.Status.online,activity=discord.Game(name="Managing the server"))
    disabled=set(storage.get_setting(0,"disabled_commands",[]))
    # These commands are core publishing tools requested for both Discord and
    # the dashboard. Re-sync on every ready event so option changes are applied
    # after startup as well as after a Discord gateway outage/reconnect.
    disabled.difference_update({"announce","poll","event"})
    storage.set_setting(0,"disabled_commands",sorted(disabled))
    for command_name in disabled:bot.tree.remove_command(command_name)
    # Guild commands update immediately. Global command changes can remain
    # cached for an hour and Discord reports those stale definitions as
    # "outdated". Use the configured single-server scope when available.
    if config.GUILD_ID:
        target=discord.Object(id=config.GUILD_ID)
        bot.tree.copy_global_to(guild=target)
        synced=await bot.tree.sync(guild=target)
    else:
        synced=await bot.tree.sync()
    print(f"Synced {len(synced)} {'server' if config.GUILD_ID else 'global'} command(s)")
    bot.add_view(TicketControls()); restored=1; failed=0
    def restore(view,label):
        nonlocal restored,failed
        try:bot.add_view(view); restored+=1
        except Exception as e:failed+=1; print(f"Could not restore {label}: {type(e).__name__}: {e}")
    for guild in bot.guilds:
        for key in storage.rows("SELECT key,value FROM settings WHERE guild_id=? AND key LIKE 'ticket_panel:%'",(guild.id,)):
            try:cfg=json.loads(key['value']); panel_key=key['key'].split(':',1)[1]; restore(TicketPanel(panel_key,cfg),key['key'])
            except Exception as e:failed+=1; print(f"Could not load {key['key']}: {type(e).__name__}: {e}")
        for row in storage.rows("SELECT key,value FROM settings WHERE guild_id=? AND key LIKE 'poll:%'",(guild.id,)):
            try:cfg=json.loads(row['value']); restore(PollView(row['key'].split(':',1)[1],cfg.get('options',[])),row['key'])
            except Exception as e:failed+=1; print(f"Could not load {row['key']}: {type(e).__name__}: {e}")
        for row in storage.rows("SELECT key,value FROM settings WHERE guild_id=? AND key LIKE 'reaction_panel:%'",(guild.id,)):
            try:cfg=json.loads(row['value']); restore(ReactionRoleView(row['key'].split(':',1)[1],cfg),row['key'])
            except Exception as e:failed+=1; print(f"Could not load {row['key']}: {type(e).__name__}: {e}")
        for row in storage.rows("SELECT key,value FROM settings WHERE guild_id=? AND key LIKE 'message_components:%'",(guild.id,)):
            try:cfg=json.loads(row['value']); view=ActionButtonView(row['key'].split(':',1)[1],cfg)
            except Exception as e:failed+=1; print(f"Could not load {row['key']}: {type(e).__name__}: {e}"); continue
            if view.is_persistent():restore(view,row['key'])
        for row in storage.rows("SELECT * FROM glue WHERE guild_id=? AND enabled=1",(guild.id,)):
            try:cfg=storage.get_setting(guild.id,f"glue_options:{row['channel_id']}",{}); restore(GlueTemplateView(row['channel_id'],cfg),f"glue:{row['channel_id']}")
            except Exception as e:failed+=1; print(f"Could not load glue:{row['channel_id']}: {type(e).__name__}: {e}")
    print(f"Restored {restored} persistent component handler(s)"+(f"; {failed} invalid configuration(s) skipped" if failed else ""))
    if not birthday_check.is_running(): birthday_check.start()
    if not giveaway_check.is_running(): giveaway_check.start()
    print(f"Ready as {bot.user} in {len(bot.guilds)} server(s)")
    domain=os.getenv("RAILWAY_PUBLIC_DOMAIN","").strip()
    url="https://"+domain if domain else f"http://127.0.0.1:{config.PORT}"
    print(f"Dashboard ready: {url}")
    if os.getenv("RAILWAY_ENVIRONMENT"):print("Railway ready: Discord connected and /health is healthy")

@bot.event
async def on_interaction(interaction):
    """Last-resort dispatcher for persistent components missed by the view registry."""
    if interaction.type is not discord.InteractionType.component:return
    data=interaction.data or {}; custom_id=str(data.get("custom_id") or "")
    if not custom_id:return
    await asyncio.sleep(.7)
    if interaction.response.is_done():return
    try:
        parts=custom_id.split(":"); prefix=parts[0]; guild_id=interaction.guild_id
        if prefix=="ticket" and len(parts)>=3 and parts[1] in ("open","select"):
            key=parts[2]; cfg=storage.get_setting(guild_id,f"ticket_panel:{key}",{})
            if cfg:return await TicketPanel(key,cfg).open_ticket(interaction,int((data.get("values") or [0])[0]) if parts[1]=="select" else 0)
        if prefix=="poll" and len(parts)>=3:
            key,index=parts[1],int(parts[2]); cfg=storage.get_setting(guild_id,f"poll:{key}",{}); options=cfg.get("options") or []
            if index<len(options):
                storage.execute("INSERT INTO poll_votes(guild_id,poll_key,user_id,option_index) VALUES(?,?,?,?) ON CONFLICT(guild_id,poll_key,user_id) DO UPDATE SET option_index=excluded.option_index",(guild_id,key,interaction.user.id,index)); return await interaction.response.send_message(f"Vote saved: **{options[index]}**",ephemeral=True)
        if prefix=="roles" and len(parts)>=3:
            key=parts[2] if parts[1]=="select" else parts[1]; cfg=storage.get_setting(guild_id,f"reaction_panel:{key}",{}); choices=cfg.get("choices") or []
            if choices:
                await interaction.response.defer(ephemeral=True)
                if parts[1]=="select":
                    selected={int(x) for x in data.get("values",[])}
                    for i,choice in enumerate(choices):
                        role=interaction.guild.get_role(int(choice.get("role_id") or 0))
                        if role and i in selected and role not in interaction.user.roles:await interaction.user.add_roles(role,reason="Recovered role menu")
                        elif role and i not in selected and role in interaction.user.roles:await interaction.user.remove_roles(role,reason="Recovered role menu")
                    return await interaction.followup.send("Your roles were updated.",ephemeral=True)
                choice=choices[int(parts[2])]; role=interaction.guild.get_role(int(choice.get("role_id") or 0))
                if role:
                    added=role not in interaction.user.roles; await (interaction.user.add_roles(role,reason="Recovered role button") if added else interaction.user.remove_roles(role,reason="Recovered role button")); return await interaction.followup.send(f"{role.mention} {'added' if added else 'removed'}.",ephemeral=True)
        if prefix in ("action","actionmenu") and len(parts)>=3:
            key,index=parts[1],int(parts[2]); cfg=storage.get_setting(guild_id,f"message_components:{key}",{})
            choices=(cfg.get("menus") or [])[index].get("options",[]) if prefix=="actionmenu" and index<len(cfg.get("menus") or []) else (cfg.get("buttons") or [])
            choice=choices[int((data.get("values") or [index])[0])] if choices else None
            if choice:
                if choice.get("action")=="confession_submit":return await interaction.response.send_modal(ConfessionSubmitModal())
                if choice.get("action")=="confession_reply":return await interaction.response.send_modal(ConfessionReplyModal(int(choice.get("confession_id") or 0)))
                if choice.get("action")=="role":
                    role=interaction.guild.get_role(int(choice.get("role_id") or 0))
                    if role:
                        await interaction.response.defer(ephemeral=True); added=role not in interaction.user.roles; await (interaction.user.add_roles(role,reason="Recovered message button") if added else interaction.user.remove_roles(role,reason="Recovered message button")); return await interaction.followup.send(f"{role.mention} {'added' if added else 'removed'}.",ephemeral=True)
                return await interaction.response.send_message(choice.get("response") or "No response has been configured.",ephemeral=choice.get("ephemeral",True))
        if prefix=="glue" and len(parts)>=3 and parts[1]=="template":
            cfg=storage.get_setting(guild_id,f"glue_options:{int(parts[2])}",{}); return await interaction.response.send_message(cfg.get("template") or "No template has been saved.",ephemeral=True)
        if prefix=="confession" and len(parts)>=2:
            if parts[1]=="submit":return await interaction.response.send_modal(ConfessionSubmitModal())
            if parts[1]=="reply" and len(parts)>=3:return await interaction.response.send_modal(ConfessionReplyModal(int(parts[2])))
        await interaction.response.send_message("This older panel's saved configuration is missing. Open it in All bot embeds and republish it once.",ephemeral=True)
    except Exception as e:
        print(f"Persistent component fallback failed for {custom_id}: {type(e).__name__}: {e}")
        try:
            if interaction.response.is_done():await interaction.followup.send("That saved action could not be completed. Check the bot's permissions and saved configuration.",ephemeral=True)
            else:await interaction.response.send_message("That saved action could not be completed. Check the bot's permissions and saved configuration.",ephemeral=True)
        except discord.HTTPException:pass

@bot.event
async def on_member_join(member):
    cfg=storage.get_setting(member.guild.id,"welcome",{})
    channel=member.guild.get_channel(int(cfg.get("channel_id") or 0))
    if cfg.get("enabled") and channel:
        edata=announcement_embed(cfg,member)
        content=variables(cfg.get("content", ""),member)
        if cfg.get("invite_tracking"):
            inviter="Unknown inviter"; code="unknown"; uses="0"
            try:
                before=storage.get_setting(member.guild.id,"invite_snapshot",{}); current=await member.guild.invites(); used=next((i for i in current if i.uses>int(before.get(i.code,0))),None)
                if used:inviter=used.inviter.mention if used.inviter else "Unknown inviter"; code=used.code; uses=str(used.uses)
                storage.set_setting(member.guild.id,"invite_snapshot",{i.code:i.uses for i in current})
            except discord.Forbidden:inviter="Invite tracking unavailable (grant Manage Server)"
            tracked=(cfg.get("invite_message") or "Invited by {inviter} using `{invite_code}` ({invite_uses} uses)").replace("{inviter}",inviter).replace("{invite_code}",code).replace("{invite_uses}",uses)
            content=(content+"\n"+tracked).strip()
        embed,files=make_embed_with_files(edata); await channel.send(content=content or None,embed=embed,files=files)
    role=member.guild.get_role(int(cfg.get("role_id") or 0))
    if role:
        try: await member.add_roles(role,reason="Welcome role")
        except discord.Forbidden: pass
    logs=storage.get_setting(member.guild.id,"action_logs",{}); account_age=datetime.now(timezone.utc)-member.created_at; threshold=max(0,int(logs.get("new_account_days",7))); recent=account_age<timedelta(days=threshold)
    creation=f"created **{age_text(account_age)} ago**"
    if recent:creation=f"⚠️ **NEW ACCOUNT** — {creation} ⚠️"
    await action_log(member.guild,"member_join",member,"Member joined",f"{member.mention} — **{ordinal(member.guild.member_count or 1)} to join**\n{creation}",[("Account created",discord.utils.format_dt(member.created_at,"F"),False)],0x57F287 if not recent else 0xED4245)

@bot.event
async def on_member_remove(member):
    cfg=storage.get_setting(member.guild.id,"leave",{}); channel=member.guild.get_channel(int(cfg.get("channel_id") or 0))
    if cfg.get("enabled") and channel:
        edata=announcement_embed(cfg,member)
        embed,files=make_embed_with_files(edata); await channel.send(content=variables(cfg.get("content", ""),member) or None,embed=embed,files=files)
    roles=[r.mention for r in member.roles if not r.is_default()]
    await action_log(member.guild,"member_leave",member,"Member left",f"{member.mention} left **{member.guild.name}**.",[("Roles",clipped(" ".join(roles) if roles else "No roles",1024),False)],0xED4245)

@bot.event
async def on_member_update(before,after):
    if before.premium_since==after.premium_since:return
    cfg=storage.get_setting(after.guild.id,"boost",{}); channel=after.guild.get_channel(int(cfg.get("channel_id") or 0))
    if cfg.get("enabled") and channel and after.premium_since:
        edata=announcement_embed(cfg,after); embed,files=make_embed_with_files(edata); await channel.send(content=variables(cfg.get("content", ""),after) or None,embed=embed,files=files)

@bot.event
async def on_voice_state_update(member,before,after):
    if before.channel==after.channel:return
    if before.channel is None and after.channel is not None:
        await action_log(member.guild,"voice",member,"Joined a voice channel",f"{member.mention} joined {after.channel.mention}.",[("Channel",after.channel.mention,False)],0x57F287)
    elif before.channel is not None and after.channel is None:
        await action_log(member.guild,"voice",member,"Left a voice channel",f"{member.mention} left {before.channel.mention}.",[("Channel",before.channel.mention,False)],0xED4245)
    else:
        await action_log(member.guild,"voice",member,"Moved voice channels",f"{member.mention} moved voice channels.",[("From",before.channel.mention,True),("To",after.channel.mention,True)],0xFEE75C)

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id==bot.user.id:return
    found=storage.rows("SELECT role_id FROM reaction_roles WHERE message_id=? AND emoji=?",(payload.message_id,str(payload.emoji)))
    guild=bot.get_guild(payload.guild_id)
    if found and guild:
        member=payload.member or await resolve_member(guild,payload.user_id); role=guild.get_role(found[0]['role_id'])
        if member and role:
            try: await member.add_roles(role,reason="Reaction role")
            except discord.Forbidden: pass

@bot.event
async def on_raw_reaction_remove(payload):
    found=storage.rows("SELECT role_id FROM reaction_roles WHERE message_id=? AND emoji=?",(payload.message_id,str(payload.emoji))); guild=bot.get_guild(payload.guild_id)
    if found and guild:
        member=await resolve_member(guild,payload.user_id); role=guild.get_role(found[0]['role_id'])
        if member and role:
            try: await member.remove_roles(role,reason="Reaction role")
            except discord.Forbidden: pass

@bot.event
async def on_message(message):
    if message.author.id==bot.user.id:
        if message.guild and message.embeds:storage.execute("INSERT OR REPLACE INTO bot_messages(message_id,guild_id,channel_id,content,embed_json,created_at) VALUES(?,?,?,?,?,?)",(message.id,message.guild.id,message.channel.id,message.content,json.dumps(embed_to_dict(message.embeds[0])),message.created_at.isoformat()))
        return
    if message.author.bot:return
    if message.guild:
        cfg=storage.get_setting(message.guild.id,"confessions",{})
        if cfg.get("enabled") and message.channel.id==int(cfg.get("channel_id") or 0):
            payloads=[]
            for attachment in message.attachments[:10]:
                try:payloads.append((await attachment.read(use_cached=True),os.path.splitext(attachment.filename)[1].lower()[:10],attachment.content_type or "application/octet-stream"))
                except discord.HTTPException:pass
            urls=[str(s.url) for s in message.stickers]
            suppressed_delete_logs.add(message.id); asyncio.get_running_loop().call_later(30,suppressed_delete_logs.discard,message.id)
            try:await message.delete()
            except discord.Forbidden:
                suppressed_delete_logs.discard(message.id)
                try:await message.author.send("I could not anonymize your confession because I need Manage Messages in the confession channel. Your original message was not reposted.")
                except discord.HTTPException:pass
                return
            try:await publish_confession(message.guild,message.content,urls,payloads)
            except Exception as e:
                print(f"Automatic confession failed: {type(e).__name__}: {e}")
                try:await message.author.send(f"Your confession could not be posted after Discord accepted the anonymous conversion. Here is a private recovery copy:\n\n{message.content or '[attachment-only confession]'}")
                except discord.HTTPException:pass
            return
    row=storage.rows("SELECT * FROM glue WHERE channel_id=? AND enabled=1",(message.channel.id,))
    if row:
        g=row[0]
        try:
            if g['message_id']: await message.channel.get_partial_message(g['message_id']).delete()
        except discord.NotFound: pass
        edata=json.loads(g['embed_json']) if g['embed_json'] else {}; embed,files=make_embed_with_files(edata); opts=storage.get_setting(message.guild.id,f"glue_options:{message.channel.id}",{}); view=GlueTemplateView(message.channel.id,opts) if opts.get("template_enabled") else None; sent=await message.channel.send(g['content'] or None,embed=embed,files=files,view=view)
        storage.execute("UPDATE glue SET message_id=? WHERE channel_id=?",(sent.id,message.channel.id))
    await bot.process_commands(message)

@bot.event
async def on_message_edit(before,after):
    if bot.user and after.author.id==bot.user.id and after.guild and after.embeds:
        storage.execute("INSERT OR REPLACE INTO bot_messages(message_id,guild_id,channel_id,content,embed_json,created_at) VALUES(?,?,?,?,?,?)",(after.id,after.guild.id,after.channel.id,after.content,json.dumps(embed_to_dict(after.embeds[0])),after.created_at.isoformat()))
        return
    if after.guild and not after.author.bot and before.content!=after.content:
        await action_log(after.guild,"message_edit",after.author,f"Message edited in #{after.channel.name}",after.author.mention,[("Before",clipped(before.content or "No text content"),False),("After",clipped(after.content or "No text content"),False)],0xFEE75C)

@bot.event
async def on_raw_message_delete(payload):
    suppressed=payload.message_id in suppressed_delete_logs; suppressed_delete_logs.discard(payload.message_id)
    old=storage.rows("SELECT guild_id,component_key FROM message_component_configs WHERE message_id=?",(payload.message_id,))
    storage.execute("DELETE FROM bot_messages WHERE message_id=?",(payload.message_id,)); storage.execute("DELETE FROM message_component_configs WHERE message_id=?",(payload.message_id,))
    if old:storage.execute("DELETE FROM settings WHERE guild_id=? AND key=?",(old[0]["guild_id"],f"message_components:{old[0]['component_key']}"))
    if suppressed:return
    message=payload.cached_message; guild=bot.get_guild(payload.guild_id) if payload.guild_id else None
    if not message or not guild or message.author.bot:return
    attachments="\n".join(a.filename for a in message.attachments) or "None"
    await action_log(guild,"message_delete",message.author,f"Message deleted in #{message.channel.name}",message.content or "No text content",[("Attachments",attachments,False)],0xED4245)

@bot.tree.command(description="Post an anonymous confession")
async def confess(interaction:discord.Interaction,message:app_commands.Range[str,1,1800]):
    await post_confession(interaction,message)

async def send_gif_action(interaction,target,action):
    cfg=storage.get_setting(interaction.guild_id,"gif_actions",{}); choices=[x for x in cfg.get(action,[]) if x]
    if not choices:return await interaction.response.send_message(f"No GIFs have been added for /{action} yet.",ephemeral=True)
    verbs={"hug":"hugs","kiss":"kisses","slap":"slaps","pat":"pats","cuddle":"cuddles with","bite":"bites"}
    chosen=random.choice(choices); content=f"{interaction.user.mention} {verbs[action]} {target.mention}"; color=(cfg.get("_colors") or {}).get(action) or "#2B2D31"; author=(cfg.get("_authors") or {}).get(action) or {}; edata={"color":color,"author":author.get("name") or "","author_icon":author.get("icon") or ""}
    if isinstance(chosen,dict) and chosen.get("asset"):
        path=os.path.join(os.path.dirname(config.DB_PATH),"uploads",os.path.basename(chosen["asset"])); file=discord.File(path,filename=os.path.basename(path)); await interaction.response.send_message(content=content,embed=make_embed({**edata,"image":f"attachment://{os.path.basename(path)}"}),file=file)
    else:await interaction.response.send_message(content=content,embed=make_embed({**edata,"image":str(chosen)}))

@bot.tree.command(description="Hug somebody")
async def hug(interaction:discord.Interaction,member:discord.Member):await send_gif_action(interaction,member,"hug")
@bot.tree.command(description="Kiss somebody")
async def kiss(interaction:discord.Interaction,member:discord.Member):await send_gif_action(interaction,member,"kiss")
@bot.tree.command(description="Slap somebody")
async def slap(interaction:discord.Interaction,member:discord.Member):await send_gif_action(interaction,member,"slap")
@bot.tree.command(description="Pat somebody")
async def pat(interaction:discord.Interaction,member:discord.Member):await send_gif_action(interaction,member,"pat")
@bot.tree.command(description="Cuddle somebody")
async def cuddle(interaction:discord.Interaction,member:discord.Member):await send_gif_action(interaction,member,"cuddle")
@bot.tree.command(description="Bite somebody")
async def bite(interaction:discord.Interaction,member:discord.Member):await send_gif_action(interaction,member,"bite")

@bot.tree.command(description="Set your birthday")
@app_commands.describe(month="1-12",day="1-31",year="Optional birth year")
async def birthday(interaction:discord.Interaction,month:app_commands.Range[int,1,12],day:app_commands.Range[int,1,31],year:int|None=None):
    try: datetime(year or 2000,month,day)
    except ValueError:return await interaction.response.send_message("That date is not valid.",ephemeral=True)
    storage.execute("INSERT INTO birthdays(guild_id,user_id,month,day,year) VALUES(?,?,?,?,?) ON CONFLICT(guild_id,user_id) DO UPDATE SET month=excluded.month,day=excluded.day,year=excluded.year",(interaction.guild_id,interaction.user.id,month,day,year))
    await interaction.response.send_message(f"Birthday saved as **{month}/{day}**.",ephemeral=True)

@bot.tree.command(description="Warn a member")
@app_commands.check(staff)
async def warn(interaction:discord.Interaction,member:discord.Member,reason:str):
    storage.execute("INSERT INTO warnings(guild_id,user_id,moderator_id,reason) VALUES(?,?,?,?)",(interaction.guild_id,member.id,interaction.user.id,reason)); await interaction.response.send_message(f"Warned {member.mention}: {reason}")

@bot.tree.command(description="Timeout a member")
@app_commands.check(staff)
async def timeout(interaction:discord.Interaction,member:discord.Member,minutes:app_commands.Range[int,1,40320],reason:str="No reason provided"):
    await member.timeout(timedelta(minutes=minutes),reason=reason); await interaction.response.send_message(f"Timed out {member.mention} for {minutes} minutes.")

@bot.tree.command(description="Kick a member")
@app_commands.check(staff)
async def kick(interaction:discord.Interaction,member:discord.Member,reason:str="No reason provided"):
    await member.kick(reason=reason); await interaction.response.send_message(f"Kicked **{member}**: {reason}")

@bot.tree.command(description="Ban a member")
@app_commands.check(staff)
async def ban(interaction:discord.Interaction,member:discord.Member,reason:str="No reason provided"):
    await member.ban(reason=reason); await interaction.response.send_message(f"Banned **{member}**: {reason}")

@bot.tree.command(description="Create a scheduled server event")
@app_commands.check(event_manager)
@app_commands.default_permissions(manage_events=True)
@app_commands.describe(starts_in_minutes="How many minutes from now",duration_minutes="How long it lasts",location="Voice channel name, game, URL, etc.")
async def event(interaction:discord.Interaction,name:str,starts_in_minutes:app_commands.Range[int,1,10080],duration_minutes:app_commands.Range[int,15,1440],location:str,description:str=""):
    start=datetime.now(timezone.utc)+timedelta(minutes=starts_in_minutes)
    created=await interaction.guild.create_scheduled_event(name=name,description=description or None,start_time=start,end_time=start+timedelta(minutes=duration_minutes),entity_type=discord.EntityType.external,privacy_level=discord.PrivacyLevel.guild_only,location=location,reason=f"Created by {interaction.user}")
    await interaction.response.send_message(f"Event created: **{created.name}**")

@bot.tree.command(description="Delete a number of recent messages")
@app_commands.check(staff)
async def purge(interaction:discord.Interaction,amount:app_commands.Range[int,1,100]):
    await interaction.response.defer(ephemeral=True); deleted=await interaction.channel.purge(limit=amount); await interaction.followup.send(f"Deleted {len(deleted)} messages.",ephemeral=True)

@bot.tree.command(description="Copy a custom emoji into this server")
@app_commands.check(staff)
async def stealemoji(interaction:discord.Interaction,emoji:str,name:str|None=None):
    match=re.search(r"<(a?):([A-Za-z0-9_]+):(\d+)>",emoji)
    if not match:return await interaction.response.send_message("Paste a custom Discord emoji such as `<:name:123>`.",ephemeral=True)
    ext="gif" if match.group(1) else "png"; data=requests.get(f"https://cdn.discordapp.com/emojis/{match.group(3)}.{ext}",timeout=10).content
    created=await interaction.guild.create_custom_emoji(name=name or match.group(2),image=data,reason=f"Stolen by {interaction.user}"); await interaction.response.send_message(f"Added {created}")

@bot.tree.command(description="Post a customizable announcement")
@app_commands.check(publisher)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(channel="Where to post (defaults to this channel)",content="Normal text above the embed",title="Embed title",description="Embed body",color="Hex color, such as #5865F2",footer="Small text at the bottom",image_url="Large image URL",thumbnail_url="Small image URL",image="Upload a large image instead of using a URL",anonymous="Hide your name and profile picture from the announcement")
async def announce(interaction:discord.Interaction,title:str,description:str,channel:discord.TextChannel|None=None,content:str="",color:str="#5865F2",footer:str="",image_url:str="",thumbnail_url:str="",image:discord.Attachment|None=None,anonymous:bool=False):
    target=channel or interaction.channel
    if not isinstance(target,(discord.TextChannel,discord.Thread)):
        return await interaction.response.send_message("Choose a text channel for the announcement.",ephemeral=True)
    permissions=target.permissions_for(interaction.guild.me)
    if not permissions.view_channel or not permissions.send_messages:
        return await interaction.response.send_message(f"I cannot send messages in {target.mention}.",ephemeral=True)
    edata={"title":title,"description":description,"color":color,"footer":footer,"image":image.url if image else image_url,"thumbnail":thumbnail_url}
    if not anonymous:edata.update(author=interaction.user.display_name,author_icon=str(interaction.user.display_avatar.url))
    await interaction.response.defer(ephemeral=True)
    try:msg=await target.send(content=content or None,embed=make_embed(edata))
    except discord.HTTPException as e:return await interaction.followup.send(f"Discord rejected the announcement: {e.text or str(e)}",ephemeral=True)
    await interaction.followup.send(f"Announcement posted in {target.mention}: {msg.jump_url}",ephemeral=True)

@bot.tree.command(description="Create a customizable button poll")
@app_commands.check(publisher)
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(channel="Where to post (defaults to this channel)",description="Extra text below the question",color="Hex color, such as #5865F2",footer="Small text at the bottom",image_url="Large image URL",thumbnail_url="Small image URL",option_1="First choice",option_2="Second choice",option_3="Optional third choice",option_4="Optional fourth choice",option_5="Optional fifth choice")
async def poll(interaction:discord.Interaction,question:str,option_1:str,option_2:str,channel:discord.TextChannel|None=None,description:str="Choose an option below.",color:str="#5865F2",footer:str="",image_url:str="",thumbnail_url:str="",option_3:str|None=None,option_4:str|None=None,option_5:str|None=None):
    target=channel or interaction.channel; options=[x.strip() for x in (option_1,option_2,option_3,option_4,option_5) if x and x.strip()]
    if not isinstance(target,(discord.TextChannel,discord.Thread)):
        return await interaction.response.send_message("Choose a text channel for the poll.",ephemeral=True)
    permissions=target.permissions_for(interaction.guild.me)
    if not permissions.view_channel or not permissions.send_messages:
        return await interaction.response.send_message(f"I cannot send messages in {target.mention}.",ephemeral=True)
    key=secrets.token_hex(6); cfg={"options":options,"question":question}; storage.set_setting(interaction.guild_id,f"poll:{key}",cfg)
    view=PollView(key,options); bot.add_view(view); embed=make_embed({"title":question,"description":description,"color":color,"footer":footer or f"Poll by {interaction.user.display_name}","image":image_url,"thumbnail":thumbnail_url})
    await interaction.response.defer(ephemeral=True)
    try:msg=await target.send(embed=embed,view=view)
    except discord.HTTPException as e:
        storage.execute("DELETE FROM settings WHERE guild_id=? AND key=?",(interaction.guild_id,f"poll:{key}")); return await interaction.followup.send(f"Discord rejected the poll: {e.text or str(e)}",ephemeral=True)
    await interaction.followup.send(f"Poll posted in {target.mention}: {msg.jump_url}",ephemeral=True)

@tasks.loop(minutes=1)
async def birthday_check():
    for guild in bot.guilds:
        cfg=storage.get_setting(guild.id,"birthdays",{})
        try:now=datetime.now(ZoneInfo(cfg.get("timezone") or "America/New_York"))
        except Exception:now=datetime.now(ZoneInfo("America/New_York"))
        today=now.date().isoformat()
        if now.hour!=0 or storage.get_setting(guild.id,"birthday_last_run","")==today:continue
        channel=guild.get_channel(int(cfg.get("channel_id") or 0))
        if not cfg.get("enabled"):continue
        for row in storage.rows("SELECT * FROM birthdays WHERE guild_id=? AND month=? AND day=?",(guild.id,now.month,now.day)):
            member=await resolve_member(guild,row['user_id'])
            if member and channel:
                edata=announcement_embed(cfg,member); embed,files=make_embed_with_files(edata); await channel.send(content=variables(cfg.get("content","Happy birthday {user}! 🎉"),member) or None,embed=embed,files=files)
        storage.set_setting(guild.id,"birthday_last_run",today)

@tasks.loop(seconds=30)
async def giveaway_check():
    due=storage.rows("SELECT * FROM giveaways WHERE ended=0 AND ends_at<=?",(datetime.now(timezone.utc).isoformat(),))
    for g in due:
        channel=bot.get_channel(g['channel_id'])
        if not channel:continue
        try: msg=await channel.fetch_message(g['message_id'])
        except discord.NotFound:continue
        entry_emoji=storage.get_setting(g['guild_id'],f"giveaway_emoji:{g['message_id']}","🎉"); reaction=next((r for r in msg.reactions if str(r.emoji)==entry_emoji),None); users=[u async for u in reaction.users()] if reaction else []; users=[u for u in users if not u.bot]
        winners=random.sample(users,min(g['winners'],len(users))) if users else []
        await channel.send(f"🎉 **{g['prize']}** winner(s): {' '.join(u.mention for u in winners) if winners else 'No valid entries.'}"); storage.execute("UPDATE giveaways SET ended=1 WHERE message_id=?",(g['message_id'],))

def run_bot():
    if not config.TOKEN or "PASTE" in config.TOKEN: raise RuntimeError("Add DISCORD_TOKEN to .env first")
    bot.run(config.TOKEN,log_handler=None)

if __name__=="__main__":
    # Prevent dashboard.py from importing a second bot instance when this file is the entry point.
    sys.modules["bot"]=sys.modules[__name__]
    from run import main
    main()
