import discord, os
import config

def has_embed_content(data):
    data=data or {}
    return any(data.get(k) for k in ("title","description","url","image","thumbnail","author","footer")) or any(f.get("name") and f.get("value") for f in data.get("fields",[]))

def make_embed(data):
    data=data or {}
    color=str(data.get("color") or "#5865F2").lstrip("#")
    try: colour=int(color,16)
    except ValueError: colour=0x5865F2
    e=discord.Embed(title=data.get("title") or None, description=data.get("description") or None, color=colour)
    if data.get("url"): e.url=data["url"]
    if data.get("image"): e.set_image(url=data["image"])
    if data.get("thumbnail"): e.set_thumbnail(url=data["thumbnail"])
    if data.get("author"): e.set_author(name=data["author"], icon_url=data.get("author_icon") or None)
    if data.get("footer"): e.set_footer(text=data["footer"], icon_url=data.get("footer_icon") or None)
    for f in data.get("fields",[])[:25]:
        if f.get("name") and f.get("value"): e.add_field(name=f["name"], value=f["value"], inline=bool(f.get("inline")))
    return e

def embed_to_dict(embed):
    if not embed:return {}
    d=embed.to_dict()
    return {"title":d.get("title",""),"description":d.get("description",""),"url":d.get("url",""),"color":f"#{d.get('color',0x5865F2):06x}","image":d.get("image",{}).get("url",""),"thumbnail":d.get("thumbnail",{}).get("url",""),"author":d.get("author",{}).get("name",""),"author_icon":d.get("author",{}).get("icon_url",""),"footer":d.get("footer",{}).get("text",""),"footer_icon":d.get("footer",{}).get("icon_url",""),"fields":d.get("fields",[])}

def make_embed_with_files(data):
    data=dict(data or {}); files=[]; upload_dir=os.path.join(os.path.dirname(config.DB_PATH),"uploads")
    for field,asset_key in (("image","image_asset"),("thumbnail","thumbnail_asset"),("author_icon","author_icon_asset"),("footer_icon","footer_icon_asset")):
        token=os.path.basename(str(data.get(asset_key) or "")); path=os.path.join(upload_dir,token)
        if token and os.path.isfile(path):
            files.append(discord.File(path,filename=token)); data[field]=f"attachment://{token}"
    return (make_embed(data) if has_embed_content(data) else None),files

def variables(text, member, extra=None):
    if not text: return text
    guild=member.guild
    result=text.replace("{user}",member.mention).replace("{username}",member.name).replace("{display_name}",member.display_name).replace("{server}",guild.name).replace("{member_count}",str(guild.member_count or 0))
    for key,value in (extra or {}).items():result=result.replace("{"+key+"}",str(value))
    return result
