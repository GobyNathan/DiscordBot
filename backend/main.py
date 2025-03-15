import discord
from discord.ext import commands
import os
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

if __name__ == "__main__":
    project_root = Path(__file__).parent.parent
    env_path = project_root / "envs" / "botenvs.env"
    
    load_env_from_file(env_path)
    
    print("Environment variables loaded successfully")

    bot = commands.Bot(command_prefix="$", intents=discord.Intents.all())
    bot.run(os.environ["DISCORD_TOKEN"])