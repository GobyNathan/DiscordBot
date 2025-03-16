import discord
from discord.ext import commands
import os
import asyncio
from pathlib import Path

def load_env_from_file(env_path):
    """Load environment variables from file into os.environ"""
    if not Path(env_path).exists():
        print(f"Warning: Environment file {env_path} not found")
        return

    with open(env_path, 'r') as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
                
            key, value = line.split('=', 1)
            os.environ[key.strip()] = value.strip()

async def load_extensions(bot):
    """Load all cog extensions"""
    # Create the cogs directory if it doesn't exist
    cogs_dir = Path(__file__).parent / "cogs"
    cogs_dir.mkdir(exist_ok=True)
    
    # Load the Music cog
    try:
        await bot.load_extension("cogs.music")
        print("Music cog loaded successfully")
    except Exception as e:
        print(f"Failed to load Music cog: {e}")

async def main():
    # Load environment variables
    project_root = Path(__file__).parent.parent
    env_path = project_root / "envs" / "botenvs.env"
    
    load_env_from_file(env_path)
    print("Environment variables loaded successfully")

    # Create and configure the bot
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="$", intents=intents, help_command=commands.DefaultHelpCommand())
    
    # Add event handlers
    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user.name} ({bot.user.id})")
        print(f"Discord.py version: {discord.__version__}")
        print("------")
        
        # Set bot status
        await bot.change_presence(activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="$play commands"
        ))
    
    # Load all cogs/extensions
    await load_extensions(bot)
    
    # Run the bot
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN not found in environment variables")
        return
        
    try:
        await bot.start(token)
    except Exception as e:
        print(f"Error starting bot: {e}")

if __name__ == "__main__":
    asyncio.run(main())