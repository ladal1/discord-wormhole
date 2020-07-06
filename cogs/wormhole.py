import re
import json
from datetime import datetime

import discord
from discord.ext import commands

from core import checks, wormcog
from core.database import repo_b, repo_u, repo_w

started = datetime.today().strftime("%Y-%m-%d %H:%M:%S")

config = json.load(open("config.json"))


class Wormhole(wormcog.Wormcog):
    """Transfer messages between guilds"""

    def __init__(self, bot):
        super().__init__(bot)

        # Global message counter
        self.transferred = 0

        # Per-channel message couter
        self.stats = {}
        for w in repo_w.listIDs():
            self.stats[str(w)] = w.messages

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore non-textchannel sources
        if not isinstance(message.channel, discord.TextChannel):
            return

        # do not act if author is bot
        if message.author.bot:
            return

        # get wormhole
        db_w = repo_w.get(message.channel.id)
        if db_w is None:
            return
        # get additional information
        db_b = repo_b.get(db_w.beam)
        db_u = repo_u.get(message.author.id)

        # do not act if message is bot command
        if message.content.startswith(config["prefix"]):
            return await self.delete(message)

        # get wormhole channel objects
        if db_b.name not in self.wormholes or len(self.wormholes[db_b.name]) == 0:
            self.reconnect(db_b.name)

        # process incoming message
        content = self.__process(message)

        # convert attachments to links
        firstline = True
        if message.attachments:
            for f in message.attachments:
                # don't add newline if message has only attachments
                if firstline:
                    content += " " + f.url
                    firstline = False
                else:
                    content += "\n" + f.url

        if len(content) < 1:
            return

        # count the message
        self.__updateStats(message)

        # send the message
        await self.send(message, db_b.name, content, files=message.attachments)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.content == after.content:
            return

        # get forwarded messages
        forwarded = None
        for m in self.sent:
            if m[0].id == after.id:
                forwarded = m
                break
        if not forwarded:
            # TODO React with cross, wait, and delete
            return

        content = self.__process(after)
        for m in forwarded[1:]:
            await m.edit(content=content)
        # TODO React with check, wait, and delete

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        # get forwarded messages
        forwarded = None
        for m in self.sent:
            if m[0].id == message.id:
                forwarded = m
                break
        if not forwarded:
            # TODO React with cross, wait, and delete
            return

        for m in forwarded[1:]:
            await m.delete()
        # TODO React with check, wait, and delete

    @commands.command()
    async def help(self, ctx: commands.Context):
        """Display help"""
        embed = discord.Embed(title="Wormhole", color=discord.Color.light_grey())
        p = config["prefix"]
        # fmt: off
        embed.add_field(value=f"**{p}e** | **{p}edit**",   name="Edit last message")
        embed.add_field(value=f"**{p}d** | **{p}delete**", name="Delete last message")
        embed.add_field(value=f"**{p}info**",              name="Connection information")
        embed.add_field(value=f"**{p}settings**",          name="Display current settings")
        embed.add_field(value=f"**{p}link**",              name="Link to GitHub repository")
        embed.add_field(value=f"**{p}invite**",            name="Bot invite link")

        db_u = repo_u.get(ctx.author.id)
        if "User" in self.bot.cogs and db_u is None:
            embed.add_field(value="**VISITOR COMMANDS**", name="\u200b", inline=False)
            embed.add_field(value=f"**{p}register**", name="Register your username")
            embed.add_field(value=f"**{p}whois**",    name="Get information about user")

        if "User" in self.bot.cogs and db_u is not None:
            embed.add_field(value="**USER COMMANDS**",   name="\u200b", inline=False)
            embed.add_field(value=f"**{p}me**",          name="Get your information")
            embed.add_field(value=f"**{p}whois**",       name="Get information about user")
            embed.add_field(value=f"**{p}set**",         name="Edit nickname or home")

        # TODO Rewrite
        if "Admin" in self.bot.cogs and db_u.mod == 1:
            embed.add_field(value=f"**MOD COMMANDS   |   {p}user edit ...**", name="\u200b", inline=False)
            embed.add_field(value="... **nickname [name]**",       name="Nickname")
            embed.add_field(value="... **readonly [true|false]**", name="Write permission")
            embed.add_field(value="... **home [wormhole]**",       name="Home guild")
        # fmt: on
        await ctx.send(embed=embed, delete_after=self.delay())
        await self.delete(ctx.message)

    @commands.guild_only()
    @commands.check(checks.in_wormhole)
    @commands.command(name="remove", aliases=["d", "delete", "r"])
    async def remove(self, ctx: commands.Context):
        """Delete last sent message"""
        if len(self.sent) == 0:
            return

        for msgs in self.sent[::-1]:
            # fmt: off
            if isinstance(msgs[0], discord.Member) and ctx.author.id == msgs[0].id \
            or isinstance(msgs[0], discord.Message) and ctx.author.id == msgs[0].author.id:
                await self.delete(ctx.message)
                for m in msgs:
                    await self.delete(m)
                break
            # fmt: on

        # TODO Remove from self.sent

    @commands.guild_only()
    @commands.check(checks.in_wormhole)
    @commands.command(name="edit", aliases=["e"])
    async def edit(self, ctx: commands.Context, *, text: str):
        """Edit last sent message

        text: A new text
        """
        if len(self.sent) == 0:
            return

        for msgs in self.sent[::-1]:
            # fmt: off
            if isinstance(msgs[0], discord.Member)  and ctx.author.id == msgs[0].id \
            or isinstance(msgs[0], discord.Message) and ctx.author.id == msgs[0].author.id:
                await self.delete(ctx.message)
                m = ctx.message
                m.content = m.content.split(" ", 1)[1]
                content = self.__process(m)
                for m in msgs:
                    try:
                        await m.edit(content=content)
                    except Exception as e:
                        await self.console.critical(
                            f"Could not edit message in {m.channel.id}", error=e
                        )
                break
            # fmt: on

    @commands.guild_only()
    @commands.check(checks.in_wormhole)
    @commands.command(aliases=["stat", "stats"])
    async def info(self, ctx: commands.Context):
        """Display information about wormholes"""
        # heading
        msg = [
            f">>> **[[total]]** messages sent in total "
            f"(**{self.transferred}** since {started}); "
            f"ping **{self.bot.latency:.2f}s**",
            "",
            "Currently opened wormholes:",
        ]
        db_w = repo_w.get(ctx.channel.id)
        db_b = repo_b.get(db_w.beam)

        wormholes = repo_w.getObjects(db_w.beam)

        # loop over wormholes in current beam
        count = 0
        for wormhole in wormholes:
            count += wormhole.messages
            line = []
            # logo
            if len(wormhole.logo):
                line.append(wormhole.logo)
            # guild, channel, counter
            channel = self.bot.get_channel(wormhole.channel)
            line.append(
                f"**{discord.utils.escape_markdown(channel.guild.name)}** "
                f"({channel.mention}): "
                f"**{self.stats[str(wormhole.channel)]}** messages"
            )
            # inactive, ro
            pars = []
            if wormhole.active is False:
                pars.append("inactive")
            if wormhole.readonly is True:
                pars.append("read only")
            if len(pars) > 0:
                line.append(f"({', '.join(pars)})")
            # join and send
            msg.append(" ".join(line))

        # in total count, include messages not yet saved into the database
        count = count + self.transferred % 50
        await ctx.send("\n".join(msg).replace("[[total]]", str(count)), delete_after=self.delay())

    @commands.guild_only()
    @commands.check(checks.in_wormhole)
    @commands.command()
    async def settings(self, ctx: commands.Context):
        """Display settings for current beam"""
        db_w = repo_w.get(ctx.channel.id)
        db_b = repo_b.get(db_w.beam)
        db_u = repo_u.get(ctx.author.id)

        msg = ">>> **Settings**:\n"
        # beam settings
        pars = []
        # fmt: off
        pars.append("active" if db_b.active else "inactive")
        pars.append(f"replacing (timeout **{db_b.timeout} s**)" if db_b.replace else "not replacing")
        pars.append(f"anonymity level **{db_b.anonymity}**")
        # fmt: on
        msg += ", ".join(pars)

        # wormhole settings
        pars = []
        if db_w.active is False:
            pars.append("inactive")
        if db_w.readonly is True:
            pars.append("read only")
        if len(pars) > 0:
            msg += "\n**Wormhole overrides**:\n"
            msg += ", ".join(pars)

        # user settings
        if db_u is not None and db_u.readonly is True:
            msg += "\n**User overrides**:\n"
            msg += "read only"

        await ctx.send(msg, delete_after=self.delay())

    @commands.command()
    async def link(self, ctx: commands.Context):
        """Send a message with link to the bot"""
        await ctx.send("> **GitHub link:** https://github.com/sinus-x/discord-wormhole")
        await self.delete(ctx.message)

    @commands.command()
    async def invite(self, ctx: commands.Context):
        """Invite the wormhole to your guild"""
        # permissions:
        # - send messages      - attach files
        # - manage messages    - use external emojis
        # - embed links        - add reactions
        m = (
            "> **Invite link:** https://discordapp.com/oauth2/authorize?client_id="
            + str(self.bot.user.id)
            + "&permissions=321600&scope=bot"
        )
        await ctx.send(m)
        await self.delete(ctx.message)

    def __getPrefix(self, message: discord.Message, firstline: bool = True):
        """Get prefix for message"""
        db_w = repo_w.get(message.channel.id)
        db_b = repo_b.get(db_w.beam)
        db_u = repo_u.get(message.author.id)

        # get user nickname
        if db_u is not None:
            name = db_u.nickname
            home = repo_w.get(db_u.home_id)
        else:
            name = self.sanitise(message.author.name, limit=32)
            home = db_w

        # get logo
        if len(home.logo):
            if firstline:
                logo = home.logo
            else:
                logo = config["logo fill"]
        else:
            logo = None

        # get prefix
        if db_b.anonymity == "none":
            # display everything
            return f"{logo} **{name}**: "
        if db_b.anonymity == "guild" and logo is not None:
            # display guild logo
            return logo + " "
        if db_b.anonymity == "guild" and logo is None:
            # display guild name
            return f"{self.sanitise(message.guild.name)}, **{name}**"
        # wrong configuration or full anonymity
        return ""

    def __process(self, message: discord.Message):
        """Escape mentions and apply anonymity"""
        content = message.content

        users = re.findall(r"<@![0-9]+>", content)
        roles = re.findall(r"<@&[0-9]+>", content)
        channels = re.findall(r"<#[0-9]+>", content)

        # prevent tagging
        for u in users:
            try:
                user = str(self.bot.get_user(int(u.replace("<@!", "").replace(">", ""))))
            except:
                user = "unknown-user"
            content = content.replace(u, user)
        for r in roles:
            try:
                role = message.guild.get_role(int(r.replace("<@&", "").replace(">", ""))).name
            except:
                role = "unknown-role"
            content = content.replace(r, role)
        # add guild to channel tag
        for channel in channels:
            try:
                ch = self.bot.get_channel(int(channel.replace("<#", "").replace(">", "")))
                guild_name = discord.utils.escape_markdown(ch.guild.name)
                content = content.replace(channel, f"{channel} __**({guild_name})**__")
            except:
                pass

        # line preprocessor for codeblocks
        if "```" in content:
            backticks = re.findall(r"```[a-z0-9]*", content)
            for b in backticks:
                content = content.replace(f" {b}", f"\n{b}", 1)
                content = content.replace(f"{b} ", f"{b}\n", 1)

        # apply prefixes
        content_ = content.split("\n")
        content = ""
        p = self.__getPrefix(message)
        code = False
        for i in range(len(content_)):
            if i == 1:
                # use fill icon instead of guild one
                p = self.__getPrefix(message, firstline=False)
            line = content_[i]
            # add prefix if message starts with code block
            if i == 0 and line.startswith("```"):
                content += self.__getPrefix(message) + "\n"
            if line.startswith("```"):
                code = True
            if code:
                content += line + "\n"
            else:
                content += p + line + "\n"
            if line.endswith("```") and code:
                code = False

        return content.replace("@", "@_")

    def __updateStats(self, message: discord.Message):
        """Increment wormhole's statistics"""
        # get wormhole ID
        author = repo_u.get(message.channel.id)
        if author is not None:
            wormhole_id = author.home_id
        else:
            wormhole_id = message.channel.id

        try:
            self.stats[str(wormhole_id)] += 1
        except KeyError:
            self.stats[str(wormhole_id)] = 1

        # save
        self.transferred += 1
        if self.transferred % 50 == 0:
            self.__saveStats()

    def __saveStats(self):
        """Save message statistics to the database"""
        for wormhole, count in self.stats.items():
            repo_w.set(int(wormhole), messages=count)


def setup(bot):
    bot.add_cog(Wormhole(bot))
