import asyncio
import os
from datetime import datetime, timedelta, timezone
import discord
from discord import app_commands, utils
from discord.channel import TextChannel
from discord.member import Member
from dotenv import load_dotenv
import re
from typing import Sequence, Optional

from messagepurge import *

GUILD_ID = discord.Object(id=771195948151603211)


class TongBot(discord.Client):
    # Suppress error on the User attribute being None since it fills up later
    user: discord.ClientUser

    def __init__(self, *, intents: discord.Intents):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)


intents = discord.Intents.default()
intents.members = True
intents.moderation = True
client = TongBot(intents=intents)

modRoleId = 1196609556755787836


class DurationTransformer(app_commands.Transformer):
    async def transform(
        self, interaction: discord.Interaction, value: str
    ) -> timedelta:
        duration = re.search("\d+[smhd]", value)
        dtime: timedelta | None = None
        duration = duration.group(0)
        num = re.search("\d+", duration)
        if "s" in duration:
            dtime = timedelta(seconds=int(num.group(0)))
        elif "m" in duration:
            dtime = timedelta(minutes=int(num.group(0)))
        elif "d" in duration:
            dtime = timedelta(days=int(num.group(0)))
        else:
            dtime = timedelta(hours=int(num.group(0)))

        return dtime


def owner_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        is_owner = interaction.user.id == interaction.guild.owner_id
        if not is_owner:
            await interaction.response.send_message(
                "You are not allowed to run that command", ephemeral=True
            )
        return is_owner

    return app_commands.check(predicate)


def owner_or_mod():
    async def predicate(interaction: discord.Interaction) -> bool:
        is_mod = interaction.user.get_role(modRoleId) is not None
        is_owner = interaction.user.id == interaction.guild.owner_id
        is_owner_or_mod = is_mod or is_owner

        if not is_owner_or_mod:
            await interaction.response.send_message(
                "You are not allowed to run that command", ephemeral=True
            )
        return is_owner_or_mod

    return app_commands.check(predicate)


@client.event
async def on_ready():
    print(f"Logged in as {client.user} (ID: {client.user.id})")
    print("------")

    # check the db for existing tasks
    tasks = await get_all_tasks_db()

    # start tasks
    for task in tasks:
        try:
            channel_id, dtime = task
            channel = client.get_channel(channel_id)
            dtime = timedelta(seconds=dtime)
            if not channel or channel.type != discord.ChannelType.text:
                # delete invalid data
                print(
                    f"channel {channel_id} is not a text channel or kms has no access to it"
                )
                await delete_task_db(channel_id)
            else:
                print(
                    f"starting purge task in guild {channel.guild} channel {channel_id} with dtime {dtime}"
                )
                await set_purge_task_loop(channel, dtime)
        except Exception as e:
            print(f"error starting task in channel {channel_id}: {e}")
            await delete_task_db(channel_id)

    # set status
    game = discord.Game(f"@{client.user.name} help")
    await client.change_presence(status=discord.Status.online, activity=game)


@client.tree.command()
async def ping(interaction: discord.Interaction):
    """Pings the bot"""
    await interaction.response.send_message("pong")


@owner_only()
@client.tree.command()
async def stop_message_purge(interaction: discord.Interaction):
    """Stops the message purge of the current channel"""
    try:
        channel_id = interaction.channel.id
        if channel_id in active_tasks:
            stop_task(channel_id)
            await delete_task_db(channel_id)
            del active_tasks[channel_id]
            await interaction.response.send_message(
                "Message purging stopped in this channel."
            )
        else:
            await interaction.response.send_message("Nothing to stop in this channel.")
    except Exception as e:
        print(e)
        await interaction.response.send_message(
            f"failed to stop purge task for channel: {e}", ephemeral=True
        )


@owner_or_mod()
@client.tree.command()
@app_commands.describe(
    ttl="Time-to-live for messages in the channel. E.g. 24h, 30s, 2d, 5m"
)
async def purge_messages(
    interaction: discord.Interaction,
    ttl: app_commands.Transform[timedelta, DurationTransformer],
):
    """Sets a task which will purge messages in the configured channel after a given time-to-live"""
    try:
        if not isinstance(interaction.channel, TextChannel):
            await interaction.response.send_message(
                "This command only works in text channels"
            )

        else:
            # start / restart task in a channel
            await set_purge_task_loop(interaction.channel, ttl)
            print(
                f"{datetime.now(timezone.utc)} updated purge task in guild {interaction.guild}"
            )
            await interaction.response.send_message(
                "Purge loop was set", ephemeral=True
            )
    except Exception as e:
        print(e)
        await interaction.response.send_message(
            f"failed to set purge task for channel: {e}", ephemeral=True
        )


@owner_only()
@app_commands.describe(dry_run="Prints users as a response rather than purging them")
@client.tree.command()
async def purge_users(interaction: discord.Interaction, dry_run: bool = False):
    """Purges all users who are not assigned specific roles"""

    await interaction.response.defer(thinking=True)

    patronRoleId = 1000851009553313883
    memberRoleId = 1441237799473905684
    deepStateRoleId = 1441184394386870424
    ownerRoleId = 812438462296752138
    standardBotRoleId = 905161586589728819

    protectedRoles = [
        patronRoleId,
        memberRoleId,
        modRoleId,
        ownerRoleId,
        deepStateRoleId,
        standardBotRoleId,
    ]

    protectedUsers: list[Member] = []
    usersToPurge: list[Member] = []
    allMembers: Sequence[Member] = client.get_all_members()

    for member in allMembers:
        roleIds = list(map(lambda role: role.id, member.roles))
        if set(protectedRoles).intersection(roleIds):
            protectedUsers.append(member)
        else:
            usersToPurge.append(member)

    userNamesToPurge = list(map(lambda member: member.name, usersToPurge))

    if dry_run:
        await interaction.followup.send(
            f"```Users to purge: {', '.join(userNamesToPurge)}```", ephemeral=True
        )
    else:
        for user in usersToPurge:
            await user.kick()

        await interaction.followup.send("Users have been kicked")


@app_commands.describe(
    duration="Duration in which to be timed out for. E.g. 1h, 1d",
    reason="Optional reason for the timeout",
)
@client.tree.command()
async def tmo(
    interaction: discord.Interaction,
    duration: app_commands.Transform[timedelta, DurationTransformer],
    reason: Optional[str],
):
    """Time yourself out for a defined duration"""
    await interaction.response.send_message(
        f"You will be timed out for {duration.total_seconds()} seconds", ephemeral=True
    )
    await interaction.user.timeout(duration, reason=reason)


async def main():
    load_dotenv()
    DISCORD_KEY = os.getenv("DISCORD_KEY")
    async with client:
        await client.start(DISCORD_KEY)


if __name__ == "__main__":
    utils.setup_logging()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # nothing to do here
        # `asyncio.run` handles the loop cleanup
        # and `self.start` closes all sockets and the HTTPClient instance.
        print("Bot stopped by user")
