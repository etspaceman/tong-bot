import aiosqlite
import discord
from discord.channel import TextChannel
from discord.message import Message
from discord.ext import tasks
from datetime import datetime, timedelta

PURGE_INTERVAL: int = 33  # in seconds
MAX_DURATION: timedelta = timedelta(days=3333)
MIN_DURATION: timedelta = timedelta(seconds=3)

active_tasks = {}  # key: channel id, value: task


async def purge_channel(
    channel: TextChannel,
    dtime: timedelta,
    self_msg_id: int,
):
    def is_self_message(msg: Message) -> bool:
        not msg.pinned and not msg.id == self_msg_id

    try:
        await channel.purge(
            limit=100,
            check=is_self_message,
            before=datetime.now() - dtime,
            oldest_first=True,
        )
    except discord.errors.Forbidden as e:
        print(f"403 error purging channel {channel.id}: {e}")
        if e.text == "Missing Access":
            stop_task(channel.id)
            await delete_task_db(channel.id)
            print(f"deleted task in channel {channel.id}")
        elif e.text == "Missing Permissions":
            stop_task(channel.id)
            await delete_task_db(channel.id)
            await channel.send("Σ(°Д°) kms stopped: missing permissions.")
    except Exception as e:
        print(f"error purging channel {channel.id}: {e}")


async def set_purge_task_loop(channel: TextChannel, dtime: timedelta):
    stop_task(channel.id)  # stop prev task if there's any

    if dtime < MIN_DURATION:
        dtime = MIN_DURATION
        formatted_duration = get_formatted_duration(MIN_DURATION)
        await channel.send(f"minimun duration to kms is {formatted_duration}.")
    if dtime > MAX_DURATION:
        dtime = MAX_DURATION
        formatted_duration = get_formatted_duration(MAX_DURATION)
        await channel.send(f"maximum duration to kms is {formatted_duration}.")

    interval: int = (
        dtime.total_seconds()
        if dtime.total_seconds() < PURGE_INTERVAL
        else PURGE_INTERVAL
    )

    # start the task
    new_task = tasks.loop(seconds=interval, reconnect=True)(purge_channel)
    formatted_duration = get_formatted_duration(dtime)
    self_msg = await channel.send(
        f"messages older than {formatted_duration} will be deleted on a rolling basis in this channel."
    )
    new_task.start(channel, dtime, self_msg.id)

    # update dict and db
    active_tasks[channel.id] = new_task
    await update_task_db(channel.id, dtime.total_seconds())


async def get_all_tasks_db():
    tasks = None
    try:
        db = await aiosqlite.connect("kms.db")  # create kms.db if it doesn't exist
        cursor = await db.cursor()
        await cursor.execute(
            "CREATE TABLE IF NOT EXISTS kms_tasks(channel_id INTEGER PRIMARY KEY, purge_duration_seconds INTEGER)"
        )  # channel id is unique across servers
        await db.commit()

        await cursor.execute("SELECT * FROM kms_tasks")
        tasks = await cursor.fetchall()
        await db.close()
    except Exception as e:
        print(e)
    finally:
        return tasks


async def update_task_db(channel_id: int, dtime_seconds: int):
    try:
        db = await aiosqlite.connect("kms.db")
        cursor = await db.cursor()

        # check if channel id is already in table
        await cursor.execute(
            f"SELECT channel_id FROM kms_tasks WHERE channel_id = {channel_id}"
        )
        result = await cursor.fetchone()
        if result == None:
            await cursor.execute(
                f"INSERT INTO kms_tasks (channel_id, purge_duration_seconds) VALUES ({channel_id}, {dtime_seconds})"
            )
        else:
            await cursor.execute(
                f"UPDATE kms_tasks SET purge_duration_seconds = {dtime_seconds} WHERE channel_id = {channel_id}"
            )
        await db.commit()
        await db.close()

    except Exception as e:
        print(e)


async def delete_task_db(channel_id: int):
    try:
        db = await aiosqlite.connect("kms.db")
        cursor = await db.cursor()
        await cursor.execute(f"DELETE FROM kms_tasks WHERE channel_id = {channel_id}")
        await db.commit()
        await db.close()
    except Exception as e:
        print(e)


def stop_task(channel_id: int):
    if channel_id in active_tasks:
        # print(f"stopping task {active_tasks[channel_id]} in channel {channel_id}")
        active_tasks[channel_id].stop()


def get_formatted_duration(dtime: timedelta):
    seconds = int(dtime.total_seconds())
    if seconds % 86400 == 0:
        days = seconds // 86400
        return str(days) + " days" if days > 1 else str(days) + " day"
    elif seconds % 3600 == 0:
        hours = seconds // 3600
        return str(hours) + " hours" if hours > 1 else str(hours) + " hour"
    elif seconds % 60 == 0:
        minutes = seconds // 60
        return str(minutes) + " minutes" if minutes > 1 else str(minutes) + " minute"
    else:
        return str(seconds) + " seconds" if seconds > 1 else str(seconds) + " second"
