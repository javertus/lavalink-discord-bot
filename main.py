import discord
import math
from discord import app_commands
import wavelink
from typing import Optional, cast

client = discord.Client(intents=discord.Intents.all())
tree = app_commands.CommandTree(client)
guilds = {}

LAVALINK_URI = "http://localhost:2333"
LAVALINK_PASSWORD = "youshallnotpass"

class song_create:
    def __init__(self, interaction: discord.Interaction, track: wavelink.Playable):
        self.embed = create_embed(interaction, track)
        self.track = track
        self.url = track.uri
        self.interaction = interaction
        self.channel = interaction.channel_id
        self.requester = interaction.user
        self.id = interaction.guild.id
        self.voice_client = interaction.guild.voice_client
        self.isPlaying = False
        self.isLoop = False
        self.isPaused = False
        self.skip = False
        self.skipValue = 0
        self.skipVoters = []

def create_embed(interaction: discord.Interaction, track: wavelink.Playable):
    embed = (discord.Embed(
        description=f'```css\n{track.title}\n```',
        color=discord.Color.blurple())
        .add_field(name='Duration', value=f'{track.length // 60000}:{(track.length // 1000) % 60:02}')
        .add_field(name='Requested by', value=interaction.user.mention)
        .add_field(name='Uploader', value=f'{track.author}')
        .add_field(name='URL', value=f'[Click]({track.uri})'))
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
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
        guilds.pop(member.guild.id)

@client.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player = payload.player
    if not player: return
    guild_id = player.guild.id

    #filters: wavelink.Filters = player.filters
    #filters.timescale.set(pitch=1, speed=1, rate=1)
    #await player.set_filters(filters)
    
    if len(guilds[guild_id]) == 1 and not guilds[guild_id][0].isLoop:
        await player.disconnect()
        guilds.pop(guild_id)
        return

    if not guilds[guild_id][0].isLoop or guilds[guild_id][0].skip: 
        guilds[guild_id].pop(0)
        guilds[guild_id][0].embed.title = "Now Playing"
        await client.get_channel(guilds[guild_id][0].channel).send(embed=guilds[guild_id][0].embed, silent=True)
    await player.play(guilds[guild_id][0].track)


@client.event
async def setup_hook():
    node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
    await wavelink.Pool.connect(nodes=[node], client=client)

@tree.command(name='play', description="Plays a song. URL or search text required")
async def play(interaction: discord.Interaction, search: str):
    try:
        await interaction.response.defer(ephemeral=False, thinking=True)
        check_voice_state(interaction)

        tracks = await wavelink.Playable.search(search)
        if not tracks:
            await interaction.followup.send(f"No match found for **{search}**.")
            return
        track = tracks[0]
        new_song = song_create(interaction, track)

        player = await ensure_player(interaction)

        guild_id = interaction.guild.id
        guilds[guild_id].append(new_song) if guilds.get(guild_id) else guilds.update({guild_id: [new_song]})

        if len(guilds[guild_id]) == 1:
            await player.play(track)
            guilds[guild_id][0].embed.title = "Now Playing"
            await interaction.followup.send(embed=guilds[guild_id][0].embed)
        else:
            song_index = len(guilds[guild_id]) - 1
            guilds[guild_id][song_index].embed.title = "Added To Queue"
            await interaction.followup.send(embed=guilds[guild_id][song_index].embed)
    except Exception as e:
        await interaction.followup.send(e)

@tree.command(name='loop', description="Loops the playing song")
async def loop(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        if guilds[interaction.guild.id][0].isLoop:
            guilds[interaction.guild.id][0].isLoop = False
            await interaction.response.send_message("Song loop removed.")
        else:
            guilds[interaction.guild.id][0].isLoop = True
            await interaction.response.send_message("Song loop enabled.")
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='skip', description="Vote to skip the song. Requester can skip immediately.")
async def skip(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        player: wavelink.Player = interaction.guild.voice_client
        if interaction.user in guilds[interaction.guild.id][0].skipVoters:
            raise app_commands.AppCommandError("You have already voted to skip this song.")
        guilds[interaction.guild.id][0].skipVoters.append(interaction.user)
        guilds[interaction.guild.id][0].skipValue += 1
        user_count = math.ceil(len(interaction.user.voice.channel.members) / 2)
        if guilds[interaction.guild.id][0].skipValue >= user_count or guilds[interaction.guild.id][0].requester == interaction.user:
            guilds[interaction.guild.id][0].skip = True
            await player.skip(force=True)
            await interaction.response.send_message("Song skipped.")
        else:
            await interaction.response.send_message(f"Voted to skip the song. {guilds[interaction.guild.id][0].skipValue}/{user_count} votes.")
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
        player: wavelink.Player = interaction.guild.voice_client
        await player.pause(True)
        guilds[interaction.guild.id][0].isPaused = True
        await interaction.response.send_message("Song paused.")
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='resume', description="Resumes the song.")
async def resume(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        player: wavelink.Player = interaction.guild.voice_client
        await player.pause(False)
        guilds[interaction.guild.id][0].isPaused = False
        await interaction.response.send_message("Song resumed.")
    except Exception as e:
        await interaction.response.send_message(e)

@tree.command(name='leave', description="Leaves the channel and clears the queue.")
async def leave(interaction: discord.Interaction):
    try:
        check_voice_state(interaction)
        player: wavelink.Player = interaction.guild.voice_client
        await player.disconnect()
        guilds.pop(interaction.guild.id, None)
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

        await player.set_filters(filters)

        await interaction.response.send_message(
            f'Changed timescale.\n'
            f'Pitch: {filters.timescale.payload.get("pitch")}, Speed: {filters.timescale.payload.get("speed")}, Rate: {filters.timescale.payload.get("rate")}'
        )

    except Exception as e:
        await interaction.response.send_message(e)

client.run("ENTER_BOT_TOKEN_HERE")
