import asyncio
import logging
import os

import discord
from dotenv import load_dotenv

from src.config.cliargs import CLIArgs
from src.utils.commandline import CommandLine
from src.bot.helper import BotHelper

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

logger = logging.getLogger()  # root logger


def configure_logging():
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('aiormq').setLevel(logging.ERROR)
    logging.getLogger('aio_pika').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)
    logging.getLogger('faster_whisper').setLevel(logging.WARNING)
    logging.getLogger('stripe').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)

    if CLIArgs.verbose:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG,
                            format='%(name)s: %(message)s')

    else:
        logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.INFO,
                            format='%(name)s: %(message)s')
    
    logger.setLevel(logging.DEBUG)
    logging.basicConfig(level=logging.DEBUG,
                        format='%(name)s: %(message)s')

if __name__ == "__main__":
    args = CommandLine.read_command_line()
    CLIArgs.update_from_args(args)

    configure_logging()

    loop = asyncio.get_event_loop()

    from src.bot.coolname_bot import CoolNameBot


    bot = CoolNameBot(loop)

    if not discord.opus.is_loaded():
        try:
            discord.opus.load_opus("opus")
        except OSError:
            discord.opus.load_opus("/usr/lib64/libopus.so")
        except Exception as e:
            logger.error(f"Error loading opus library: {e}")
            raise e

    @bot.event
    async def on_voice_state_update(member, before, after):
        if member.id == bot.user.id:
            # If the bot left the "before" channel
            if after.channel is None:
                guild_id = before.channel.guild.id
                helper = bot.guild_to_helper.get(guild_id, None)
                if helper:
                    helper.set_vc(None)
                    bot.guild_to_helper.pop(guild_id, None)

                bot._close_and_clean_sink_for_guild(guild_id)

    @bot.slash_command(name="connect", description="Connect to your voice channel.")
    async def connect(ctx: discord.context.ApplicationContext):
        if bot._is_ready is False:
            await ctx.respond("I am not ready yet. Try again later.", ephemeral=True)
            return
        author_vc = ctx.author.voice
        if not author_vc:
            await ctx.respond("You are not in a voice channel.", ephemeral=True)
            return

        await ctx.trigger_typing()
        try:
            guild_id = ctx.guild_id
            vc = await author_vc.channel.connect()
            helper = bot.guild_to_helper.get(guild_id, BotHelper(bot))
            helper.guild_id = guild_id
            helper.set_vc(vc)
            bot.guild_to_helper[guild_id] = helper
            await ctx.respond(f"Connected to {author_vc.channel.name}.", ephemeral=True)
            await ctx.guild.change_voice_state(channel=author_vc.channel, self_mute=True)
        except Exception as e:
            await ctx.respond(f"{e}", ephemeral=True)

    @bot.slash_command(name="transcribe", description="Transcribe the voice channel.")
    async def transcribe(ctx: discord.context.ApplicationContext):
        await ctx.trigger_typing()
        bot.start_recording(ctx)
        await ctx.respond("Starting to transcribe.", ephemeral=True)
    
    @bot.slash_command(name="stop", description="Stop the transcription.")
    async def stop(ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        helper = bot.guild_to_helper.get(guild_id, None)
        if not helper:
            await ctx.respond("I am not in your voice channel.", ephemeral=True)
            return

        bot_vc = helper.vc
        if not bot_vc:
            await ctx.respond("I am not in your voice channel.", ephemeral=True)
            return

        if not bot.guild_is_recording.get(guild_id, False):
            await ctx.respond("I am not transcribing.", ephemeral=True)
            return

        await ctx.trigger_typing()
        if bot.guild_is_recording.get(guild_id, False):
            bot.stop_recording(ctx)
        
    @bot.slash_command(name="disconnect", description="Disconnect from your voice channel.")
    async def disconnect(ctx: discord.context.ApplicationContext):
        guild_id = ctx.guild_id
        helper = bot.guild_to_helper[guild_id]
        bot_vc = helper.vc
        if not bot_vc:
            await ctx.respond("I am not in your voice channel.", ephemeral=True)
            return

        await ctx.trigger_typing()
        await bot_vc.disconnect()
        helper.guild_id = None
        helper.set_vc(None)
        bot.guild_to_helper.pop(guild_id, None)

        await ctx.respond("Disconnected from VC.", ephemeral=True)


    @bot.slash_command(name="help", description="Show the help message.")
    async def help(ctx: discord.context.ApplicationContext):
        embed_fields = [
            discord.EmbedField(
                name="/connect", value="Connect to your voice channel.", inline=True),
            discord.EmbedField(
                name="/disconnect", value="Disconnect from your voice channel.", inline=True),
            discord.EmbedField(
                name="/transcribe", value="Transcribe the voice channel.", inline=True),
            discord.EmbedField(
                name="/stop", value="Stop the transcription.", inline=True),
            discord.EmbedField(
                name="/help", value="Show the help message.", inline=True),
        ]

        embed = discord.Embed(title="Volo Help 📖",
                              description="""Volo is a bot that can record your voice channel and transcribe it. 🔉 ➡️ 📃""",
                              color=discord.Color.blue(),
                              fields=embed_fields)

        await ctx.respond(embed=embed, ephemeral=True)



    try:
        loop.run_until_complete(bot.start(DISCORD_BOT_TOKEN))
    except KeyboardInterrupt:
        logger.info("^C received, shutting down...")
        asyncio.run(bot.stop_and_cleanup())
    finally:
        # Close all connections
        loop.run_until_complete(bot.close_consumers())

        tasks = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in tasks:
            task.cancel()
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

        # Close the loop
        loop.run_until_complete(bot.close())
        loop.close()