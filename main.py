import discord
import wavelink
import math
import asyncio
from discord import app_commands
from typing import Optional, cast

client = discord.Client(intents=discord.Intents.all())
tree = app_commands.CommandTree(client)
guilds = {}

LAVALINK_URI = "http://localhost:2333"
LAVALINK_PASSWORD = "youshallnotpass"

class song_create:
    def __init__(self, interaction: discord.Interaction, track: wavelink.Playable):
        self.embed: discord.Embed = create_embed(interaction, track)
        self.track: wavelink.Playable = track
        self.url: str = track.uri
        self.interaction: discord.Interaction = interaction
        self.channel: int = interaction.channel_id
        self.requester: discord.User = interaction.user
        self.id: int = interaction.guild.id
        self.voice_client: wavelink.Player = interaction.guild.voice_client
        self.is_loop = False
        self.is_paused = False
        self.skip = False
        self.skip_value = 0
        self.position: int = 0
        self.skip_voters: dict = []
        self.message_id: int = None
        self.lock: asyncio.Lock = asyncio.Lock()

def create_embed(interaction: discord.Interaction, track: wavelink.Playable,position: int = 0, show_progress: bool = False, title: str = "Now Playing", color: discord.Color = discord.Color.blurple()):
    total_seconds = track.length // 1000
    current_seconds = position // 1000
    remaining_seconds = total_seconds - current_seconds

    embed = discord.Embed(
        description = f'```css\n{track.title}\n```',
        title = title,
        color = color
    )

    if show_progress:
        bar_length = 30
        filled_length = int(bar_length * current_seconds / total_seconds)
        bar = ("**" if filled_length else "") + "-" * filled_length + ("**" if filled_length else "") + "🔘" + "-" * (bar_length - filled_length)

        embed.add_field(
            name='Duration',
            value=(
                f'{current_seconds // 60}:{current_seconds % 60:02} / '
                f'{total_seconds // 60}:{total_seconds % 60:02} '
                f'(`{remaining_seconds // 60}:{remaining_seconds % 60:02}` left)'
            )
        )
        embed.add_field(name='Progress', value=bar, inline=False)
    else:
        embed.add_field(name='Duration', value=f'{total_seconds // 60}:{total_seconds % 60:02}')

    embed.add_field(name='Requested by', value=interaction.user.mention)
    embed.add_field(name='Uploader', value=f'{track.author}')
    embed.add_field(name='URL', value=f'[Click]({track.uri})')
    if track.artwork: embed.set_thumbnail(url=track.artwork)

    return embed

def check_voice_state(interaction: discord.Interaction):
    if not interaction.user.voice or not interaction.user.voice.channel:
        raise app_commands.AppCommandError("You are not connected to a voice channel.")
    if interaction.guild.voice_client:
        if interaction.guild.voice_client.channel != interaction.user.voice.channel:
            raise app_commands.AppCommandError("You must be in the same voice channel as the bot to use this command.")
    elif interaction.command.name != "play":
        raise app_commands.AppCommandError("The bot is not connected to any voice channel.")

async def ensure_player(interaction: discord.Interaction):
    player: wavelink.Player = interaction.guild.voice_client
    if not player:
        player = await interaction.user.voice.channel.connect(cls=wavelink.Player)
        player.autoplay = wavelink.AutoPlayMode.disabled
    return player

async def update_now_playing_status(song: song_create, status: str, position: int = 0, color: discord.Color = discord.Color.blurple()):
    async with song.lock:
        try:
            channel = client.get_channel(song.channel)
            if not channel:
                return
            msg = await channel.fetch_message(song.message_id)
            embed = create_embed(song.interaction, song.track, position, True, status, color)
            await msg.edit(embed=embed)
        except Exception as e:
            print(f"Failed to update status: {e}")

async def edit_message_by_id(channel, message_id, new_embed):
    try:
        message = await channel.fetch_message(message_id)
        await message.edit(embed=new_embed)
    except discord.NotFound:
        print(f"Message {message_id} not found")
    except discord.Forbidden:
        print("No permission to edit message")

async def update_now_playing(guild_id: int):
    song: song_create = guilds[guild_id][0]
    while True:
        async with song.lock:
            if guild_id not in guilds:
                break
            if song.message_id != guilds[guild_id][0].message_id:
                song = guilds[guild_id][0]
                continue

            player: wavelink.Player = song.voice_client
            song.position = player.position

            title = "Now Playing"
            if song.is_loop: title += " (Loop)"
            if song.is_paused: title += " (Paused)"

            song.embed = create_embed(song.interaction, song.track, song.position, True, title)
            try:
                channel = client.get_channel(song.channel)
                if channel:
                    msg = await channel.fetch_message(song.message_id)
                    await msg.edit(embed=song.embed)
            except Exception as e:
                print(f"Update embed failed: {e}")

            await asyncio.sleep(5)

@client.event
async def on_ready():
    await tree.sync()
    await client.change_presence(status=discord.Status.idle)
    print(f'Logged in as {client.user}')

@client.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload):
    print(f"Lavalink Node connected: {payload.node.identifier}")

@client.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState): # Checks if bot kicked from voice chat to clear queue
    if before.channel is None: return # to check if bot just connected to a vc
    if not guilds.get(member.guild.id): return # to check if bot just disconnected after player's queue ended
    if member.id != client.user.id: return
    if after.channel is None:
        song: song_create = guilds[member.guild.id][0]
        client.loop.create_task(update_now_playing_status(song, "Bot Kicked", song.position, discord.Color.red()))
        guilds.pop(member.guild.id)

@client.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player = payload.player
    if not player: return
    guild_id = player.guild.id
    song: song_create = guilds[guild_id][0]
    
    #filters: wavelink.Filters = player.filters
    #filters.timescale.set(pitch=1, speed=1, rate=1)
    #await player.set_filters(filters)

    if payload.reason == "finished":
        client.loop.create_task(update_now_playing_status(song, "Finished playing", song.track.length, discord.Color.green()))

    if not song.is_loop or song.skip:
        if len(guilds[guild_id]) == 1:
            guilds.pop(guild_id)
            await player.disconnect()
            return
        guilds[guild_id].pop(0)
        song: song_create = guilds[guild_id][0]
        song.embed = create_embed(song.interaction, song.track, 0, show_progress=True)
        msg = await client.get_channel(song.channel).send(embed=song.embed, silent=True)
        song.message_id = msg.id
    await player.play(song.track)


@client.event
async def setup_hook():
    node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
    await wavelink.Pool.connect(nodes=[node], client=client)

@tree.command(name='play', description="Plays a song. URL or search text required")
async def play(interaction: discord.Interaction, search: str):
    try:
        check_voice_state(interaction)

        await interaction.response.defer(ephemeral=False, thinking=True)
        tracks = await wavelink.Playable.search(search)
        if not tracks:
            await interaction.followup.send(f"No match found for **{search}**.")
            return
        track = tracks[0]

        player = await ensure_player(interaction)
        new_song = song_create(interaction, track)

        guild_id = interaction.guild.id
        guilds[guild_id].append(new_song) if guilds.get(guild_id) else guilds.update({guild_id: [new_song]})

        if len(guilds[guild_id]) == 1:
            await player.play(track)
            song: song_create = guilds[guild_id][0]
            song.embed = create_embed(interaction, track, 0, show_progress=True)
            msg = await interaction.followup.send(embed=song.embed)
            song.message_id = msg.id
            client.loop.create_task(update_now_playing(guild_id))
        else:
            song_index = len(guilds[guild_id]) - 1
            guilds[guild_id][song_index].embed.title = "Added To Queue"
            guilds[guild_id][song_index].embed.color = discord.Color.greyple()
            await interaction.followup.send(embed=guilds[guild_id][song_index].embed)
    except Exception as e:
        await interaction.followup.send(e)

@tree.command(name='loop', description="Loops the playing song")
async def loop(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        song: song_create = guilds[interaction.guild.id][0]
        if song.is_loop:
            song.is_loop = False
            await interaction.response.send_message("Song loop removed.")
        else:
            song.is_loop = True
            await interaction.response.send_message("Song loop enabled.")
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='skip', description="Vote to skip the song. Requester can skip immediately.")
async def skip(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        song: song_create = guilds[interaction.guild.id][0]
        player: wavelink.Player = interaction.guild.voice_client
        if interaction.user in song.skip_voters:
            raise app_commands.AppCommandError("You have already voted to skip this song.")
        song.skip_voters.append(interaction.user)
        song.skip_value += 1
        user_count = math.ceil(len(interaction.user.voice.channel.members) / 2)
        if song.skip_value >= user_count or song.requester == interaction.user:
            song.skip = True
            client.loop.create_task(update_now_playing_status(song, "Skipped", song.voice_client.position, discord.Color.yellow()))
            await interaction.response.send_message("Song skipped.")
            await player.skip(force=True)
        else:
            await interaction.response.send_message(f"Voted to skip the song. {song.skip_value}/{user_count} votes.")
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='queue', description="Shows the queue.")
async def queue(interaction: discord.Interaction, page: int = 1):
    try:
        check_voice_state(interaction)
        items_per_page = 10
        pages = math.ceil(len(guilds[interaction.guild.id]) / items_per_page)
        start = (page - 1) * items_per_page
        end = start + items_per_page
        queue_text = ''
        for i, song in enumerate(guilds[interaction.guild.id][start:end], start=start):
            queue_text += f'`{i + 1}.` [**{song.track.title}**]({song.track.uri})\n'
        embed = (discord.Embed(description=f'**{len(guilds[interaction.guild.id])} songs:**\n\n{queue_text}')
                 .set_footer(text=f'Page {page}/{pages}'))
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='pause', description="Pauses the song.")
async def pause(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        song: song_create = guilds[interaction.guild.id][0]
        player: wavelink.Player = interaction.guild.voice_client
        await player.pause(True)
        song.is_paused = True
        await interaction.response.send_message("Song paused.")
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='resume', description="Resumes the song.")
async def resume(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        song: song_create = guilds[interaction.guild.id][0]
        player: wavelink.Player = interaction.guild.voice_client
        await player.pause(False)
        song.is_paused = False
        await interaction.response.send_message("Song resumed.")
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='leave', description="Leaves the channel and clears the queue.")
async def leave(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        song: song_create = guilds[interaction.guild.id][0]
        player: wavelink.Player = interaction.guild.voice_client
        await player.disconnect()
        client.loop.create_task(update_now_playing_status(song, "Bot Leaved", song.voice_client.position, discord.Color.orange()))
        guilds.pop(interaction.guild.id)
        await interaction.response.send_message("Disconnected.")
    except Exception as e:
        await interaction.response.send_message(e)


@tree.command(name="timescale", description="Change pitch, speed and rate values of the player (0-100).")
async def timescale(
    interaction: discord.Interaction,
    pitch: Optional[float] = None,
    speed: Optional[float] = None,
    rate: Optional[float] = None
):
    try:
        check_voice_state(interaction)

        player: wavelink.Player = cast(wavelink.Player, interaction.guild.voice_client)

        for name, value in [("Pitch", pitch), ("Speed", speed), ("Rate", rate)]:
            if value is not None and (value < 0.1 or value > 100):
                await interaction.response.send_message(f"{name} must be between 0.1 and 100")
                return

        filters: wavelink.Filters = player.filters
        filters.timescale.set(
            pitch = pitch if pitch else (filters.timescale.payload.get("pitch") or 1.0),
            speed = speed if speed else (filters.timescale.payload.get("speed") or 1.0),
            rate = rate if rate else (filters.timescale.payload.get("rate") or 1.0)
        )

        await player.set_filters(filters, seek=True)

        await interaction.response.send_message(
            f'Changed timescale.\n'
            f'Pitch: {filters.timescale.payload.get("pitch")}, Speed: {filters.timescale.payload.get("speed")}, Rate: {filters.timescale.payload.get("rate")}'
        )

    except Exception as e:
        await interaction.response.send_message(e)

client.run("ENTER_BOT_TOKEN_HERE")
