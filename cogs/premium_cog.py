import httpx
import discord
from discord import app_commands
from discord.ext import commands
from config import GUMROAD_PRODUCT_ID, PREMIUM_URL, PREMIUM_PRICE
import database as db


async def verify_gumroad_key(license_key: str) -> tuple[bool, str]:
    if not GUMROAD_PRODUCT_ID:
        return False, "Gumroad product ID not configured. Contact an admin."
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.gumroad.com/v2/licenses/verify",
                data={"product_id": GUMROAD_PRODUCT_ID, "license_key": license_key},
            )
        data = resp.json()
        if data.get("success"):
            if data.get("purchase", {}).get("refunded"):
                return False, "This license key has been refunded."
            return True, "valid"
        return False, data.get("message", "Invalid license key.")
    except Exception as e:
        return False, f"Could not reach Gumroad: {e}"


class PremiumCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="premium", description="View your premium status or activate a license key")
    @app_commands.describe(license_key="Your Gumroad license key (leave blank to check status)")
    async def premium(self, interaction: discord.Interaction, license_key: str = None):
        await interaction.response.defer(ephemeral=True)

        if not license_key:
            info = await db.get_premium_info(interaction.user.id)
            if info:
                embed = discord.Embed(
                    title="⭐ Premium Active",
                    description=(
                        "You have an active premium subscription.\n\n"
                        "**Perks:**\n"
                        "✨ Wider matchmaking range (find matches faster)\n"
                        "⭐ Premium badge on leaderboard & profile\n"
                        "🏆 Priority queue position\n"
                        "📊 Full season history"
                    ),
                    color=discord.Color.purple(),
                )
                embed.set_footer(text=f"Activated: {info['activated_at'][:10]}")
            else:
                price_str = f"**${PREMIUM_PRICE}**" if PREMIUM_PRICE else "available now"
                buy_line = f"[Purchase on Gumroad]({PREMIUM_URL})" if PREMIUM_URL else "Purchase on Gumroad"
                embed = discord.Embed(
                    title="⭐ Syntrix Premium",
                    description=(
                        f"Upgrade for {price_str} and get matched faster.\n\n"
                        "**Perks:**\n"
                        "✨ 1.5× wider matchmaking range\n"
                        "⭐ Premium badge on leaderboard & profile\n"
                        "🏆 Priority queue position\n"
                        "📊 Full season history\n"
                        "🎮 Up to 3 game queues per server\n\n"
                        f"{buy_line}, then activate with:\n"
                        "`/premium license_key:<your-key>`"
                    ),
                    color=discord.Color.from_str("#7c3aed"),
                )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        existing = await db.get_premium_info(interaction.user.id)
        if existing:
            await interaction.followup.send("You already have premium activated.", ephemeral=True)
            return

        valid, message = await verify_gumroad_key(license_key)
        if not valid:
            await interaction.followup.send(f"License key invalid: {message}", ephemeral=True)
            return

        await db.get_or_create_player(interaction.user.id, str(interaction.user))
        await db.grant_premium(interaction.user.id, license_key)

        embed = discord.Embed(
            title="⭐ Premium Activated!",
            description=(
                "Welcome to **Syntrix Premium**!\n\n"
                "**Your perks are now active:**\n"
                "✨ Wider matchmaking range\n"
                "⭐ Premium badge on leaderboard & profile\n"
                "🏆 Priority queue position\n"
                "📊 Full season history"
            ),
            color=discord.Color.purple(),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
