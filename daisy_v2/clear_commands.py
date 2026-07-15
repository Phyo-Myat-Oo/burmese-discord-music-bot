from __future__ import annotations

import asyncio

import discord
from discord import app_commands

from .config import load_settings


class CommandCleaner(discord.Client):
    def __init__(self, *, guild_id: int | None) -> None:
        super().__init__(intents=discord.Intents.none())
        self.guild_id = guild_id
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self) -> None:
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        print("Cleared global slash commands.")

        if self.guild_id:
            guild = discord.Object(id=self.guild_id)
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"Cleared guild slash commands for {self.guild_id}.")

        await self.close()


async def main_async() -> None:
    settings = load_settings()
    if not settings.token:
        raise SystemExit("DISCORD_TOKEN is missing.")
    client = CommandCleaner(guild_id=settings.guild_id)
    await client.start(settings.token)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
