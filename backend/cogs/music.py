import discord
from discord.ext import commands
import asyncio
import yt_dlp
import os
from async_timeout import timeout
from functools import partial
import itertools

# Suppress noise about console usage from errors
yt_dlp.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # Bind to ipv4 since ipv6 can cause issues
}

ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        self.webpage_url = data.get('webpage_url')
        self.uploader = data.get('uploader')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        
        if 'entries' in data:
            # Take first item from a playlist
            data = data['entries'][0]
            
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class MusicPlayer:
    """A class which is assigned to each guild using the bot for music playback.
    
    This class implements a queue and loop, which allows for different guilds to listen to different playlists
    simultaneously.
    
    When the bot disconnects from the voice it's instance will be destroyed.
    """

    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'volume', 'loop')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.volume = 0.5
        self.current = None
        self.loop = False

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                async with timeout(300):  # 5 minutes...
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            # If we're looping, add the current song back to the queue
            if self.loop and self.current:
                await self.queue.put(self.current)

            self.current = source

            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            await self._channel.send(f'🎵 Now playing: **{source.title}**')

            await self.next.wait()

            # Make sure the FFmpeg process is cleaned up.
            source.cleanup()
            self.current = None

    def destroy(self, guild):
        """Disconnect and cleanup the player."""
        return self.bot.loop.create_task(self._cog.cleanup(guild))

class Music(commands.Cog):
    """Music related commands."""

    __slots__ = ('bot', 'players')

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    def get_player(self, ctx):
        """Retrieve the guild player, or generate one."""
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    @commands.command(name='join', aliases=['connect'])
    async def connect_(self, ctx, *, channel: discord.VoiceChannel=None):
        """Connect to voice.
        Parameters
        ------------
        channel: discord.VoiceChannel [Optional]
            The channel to connect to. If a channel is not specified, an attempt to join the voice channel you are in
            will be made.
        """
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                await ctx.send('No channel to join. Please either specify a valid channel or join one.')
                return False

        vc = ctx.voice_client

        if vc:
            if vc.channel.id == channel.id:
                return True
            try:
                await vc.move_to(channel)
            except asyncio.TimeoutError:
                await ctx.send(f'Moving to channel: <{channel}> timed out.')
                return False
        else:
            try:
                await channel.connect()
            except asyncio.TimeoutError:
                await ctx.send(f'Connecting to channel: <{channel}> timed out.')
                return False

        await ctx.send(f'Connected to: **{channel}**')
        return True

    @commands.command(name='play', aliases=['p'])
    async def play_(self, ctx, *, search: str):
        """Request a song and add it to the queue.
        This command attempts to join a valid voice channel if the bot is not already in one.
        Uses YTDL to automatically search and retrieve a song.
        Parameters
        ------------
        search: str [Required]
            The song to search and retrieve using YTDL. This could be a simple search, an ID or URL.
        """
        await ctx.typing()

        vc = ctx.voice_client

        if not vc:
            success = await ctx.invoke(self.connect_)
            if not success:
                return

        player = self.get_player(ctx)

        # If download is False, source will be a dict which will be used later to regather the stream.
        # If download is True, source will be a discord.FFmpegPCMAudio with a VolumeTransformer.
        try:
            source = await YTDLSource.from_url(search, loop=self.bot.loop, stream=True)
        except Exception as e:
            await ctx.send(f'An error occurred while processing this request: {str(e)}')
        else:
            if not player.current:
                # If nothing is playing, play it immediately
                await player.queue.put(source)
                await ctx.send(f'Added **{source.title}** to the queue.')
            else:
                # Otherwise add to queue
                await player.queue.put(source)
                await ctx.send(f'Added **{source.title}** to the queue.')

    @commands.command(name='pause')
    async def pause_(self, ctx):
        """Pause the currently playing song."""
        vc = ctx.voice_client

        if not vc or not vc.is_playing():
            return await ctx.send('I am not currently playing anything!')
        elif vc.is_paused():
            return await ctx.send('I am already paused!')

        vc.pause()
        await ctx.send('⏸️ Paused the current track!')

    @commands.command(name='resume')
    async def resume_(self, ctx):
        """Resume the currently paused song."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently playing anything!')
        elif not vc.is_paused():
            return await ctx.send('I am not currently paused!')

        vc.resume()
        await ctx.send('▶️ Resumed the current track!')

    @commands.command(name='skip')
    async def skip_(self, ctx):
        """Skip the song."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently playing anything!')

        if vc.is_paused():
            pass
        elif not vc.is_playing():
            return await ctx.send('I am not currently playing anything!')

        vc.stop()
        await ctx.send('⏭️ Skipped the current track!')

    @commands.command(name='queue', aliases=['q', 'playlist'])
    async def queue_info(self, ctx):
        """Retrieve a basic queue of upcoming songs."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!')

        player = self.get_player(ctx)
        if player.queue.empty():
            return await ctx.send('There are currently no more queued songs.')

        # Grab up to 5 entries from the queue...
        upcoming = list(itertools.islice(player.queue._queue, 0, 5))

        fmt = '\n'.join(f'**{_["title"]}**' for _ in upcoming)
        embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt, color=discord.Color.blue())

        await ctx.send(embed=embed)

    @commands.command(name='nowplaying', aliases=['np', 'current', 'currentsong', 'playing'])
    async def now_playing_(self, ctx):
        """Display information about the currently playing song."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!')

        player = self.get_player(ctx)
        if not player.current:
            return await ctx.send('I am not currently playing anything!')

        embed = discord.Embed(title='Now Playing', color=discord.Color.green())
        embed.add_field(name='Title', value=player.current.title, inline=False)
        
        if player.current.uploader:
            embed.add_field(name='Uploader', value=player.current.uploader, inline=True)
        
        if player.current.duration:
            minutes, seconds = divmod(player.current.duration, 60)
            embed.add_field(name='Duration', value=f'{int(minutes)}:{int(seconds):02d}', inline=True)
            
        if player.current.webpage_url:
            embed.add_field(name='URL', value=f"[Click Here]({player.current.webpage_url})", inline=False)
            
        if player.current.thumbnail:
            embed.set_thumbnail(url=player.current.thumbnail)

        await ctx.send(embed=embed)

    @commands.command(name='volume', aliases=['vol'])
    async def change_volume(self, ctx, *, volume: int):
        """Change the player volume.
        Parameters
        ------------
        volume: int [Required]
            The volume to set the player to in percentage. This must be between 1 and 100.
        """
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!')

        if not 0 < volume < 101:
            return await ctx.send('Please enter a value between 1 and 100.')

        player = self.get_player(ctx)

        if vc.source:
            vc.source.volume = volume / 100

        player.volume = volume / 100
        await ctx.send(f'**`{ctx.author}`**: Set the volume to **{volume}%**')

    @commands.command(name='stop')
    async def stop_(self, ctx):
        """Stop the currently playing song and clear the queue."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently playing anything!')

        player = self.get_player(ctx)
        
        # Clear the queue
        while not player.queue.empty():
            player.queue.get_nowait()
            
        vc.stop()
        await ctx.send('⏹️ Stopped playing and cleared the queue!')

    @commands.command(name='leave', aliases=['quit', 'dc'])
    async def leave_(self, ctx):
        """Stop the player and leave the voice channel."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!')

        await self.cleanup(ctx.guild)
        await ctx.send('👋 Disconnected from the voice channel!')

    @commands.command(name='loop')
    async def loop_(self, ctx):
        """Toggle loop mode."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!')

        player = self.get_player(ctx)
        player.loop = not player.loop
        
        await ctx.send(f'🔄 Loop mode is now {"enabled" if player.loop else "disabled"}')

async def setup(bot):
    await bot.add_cog(Music(bot))