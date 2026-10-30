"""Discord Components V2 response cards with native dividers and headings."""
import discord

COLOURS = {'info': 0x3498DB, 'success': 0x2ECC71, 'danger': 0xED4245, 'warning': 0xF1C40F, 'brand': 0x155E32}


class RichCard(discord.ui.LayoutView):
    def __init__(self, title, body='', *, sections=(), footer=None, tone='brand', controls=None, image=None, filename=None):
        super().__init__(timeout=None)
        children=[discord.ui.TextDisplay('## '+title)]
        if body:
            children.append(discord.ui.TextDisplay(body))
        for heading,text in sections:
            children.extend([discord.ui.Separator(),discord.ui.TextDisplay('### '+heading),discord.ui.TextDisplay(text or '—')])
        if image:
            children.extend([discord.ui.Separator(),discord.ui.MediaGallery(discord.MediaGalleryItem(image))])
        if filename:
            children.extend([discord.ui.Separator(),discord.ui.File('attachment://'+filename)])
        if controls:
            buttons=list(controls.children)
            for button in buttons:
                controls.remove_item(button)
            for start in range(0,len(buttons),5):
                children.append(discord.ui.ActionRow(*buttons[start:start+5]))
        if footer:
            children.extend([discord.ui.Separator(),discord.ui.TextDisplay('-# '+footer)])
        self.add_item(discord.ui.Container(*children,accent_colour=COLOURS.get(tone,COLOURS['brand'])))


def card_from_embed(embed, *, controls=None, tone='brand'):
    if tone=='info' and embed.colour:
        tone={0xED4245:'danger',0xE74C3C:'danger',0x2ECC71:'success',0xF1C40F:'warning'}.get(embed.colour.value,tone)
    title=embed.title or 'AU Schools'
    if title and ord(title[0])<256:
        title={'info':'ℹ️ ','success':'✅ ','danger':'❌ ','warning':'⚠️ ','brand':'🟢 '}.get(tone,'')+title
    return RichCard(title, embed.description or '',
        sections=[(field.name,field.value) for field in embed.fields], controls=controls,
        image=embed.image.url if embed.image else None, tone=tone)


def component_text(components):
    parts=[]
    for component in components:
        data=component.to_dict() if hasattr(component,'to_dict') else component
        def walk(node):
            if isinstance(node,dict):
                if node.get('type')==10 and node.get('content'):
                    parts.append(node['content'])
                for value in node.values():
                    if isinstance(value,(dict,list)):
                        walk(value)
            elif isinstance(node,list):
                for child in node:
                    walk(child)
        walk(data)
    return '\n\n'.join(parts)


def message_image(message):
    for embed in message.embeds:
        if embed.image:
            return embed.image.url
    def find(node):
        if isinstance(node,dict):
            media=node.get('media')
            if isinstance(media,dict) and media.get('url'):
                return media['url']
            for value in node.values():
                if isinstance(value,(dict,list)):
                    result=find(value)
                    if result:
                        return result
        elif isinstance(node,list):
            for child in node:
                result=find(child)
                if result:
                    return result
    for component in getattr(message,'components',[]):
        result=find(component.to_dict() if hasattr(component,'to_dict') else component)
        if result:
            return result
    return None
