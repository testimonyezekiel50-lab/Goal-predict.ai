
import os
import math
import logging
import httpx

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
API_KEY = os.getenv("API_FOOTBALL_KEY")
BASE_URL = os.getenv(
    "API_FOOTBALL_BASE",
    "https://v3.football.api-sports.io"
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


async def api_get(endpoint, params):
    if not API_KEY:
        raise RuntimeError("API_FOOTBALL_KEY is not configured")

    headers = {"x-apisports-key": API_KEY}

    async with httpx.AsyncClient(timeout=25) as client:
        response = await client.get(
            f"{BASE_URL}/{endpoint}",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        data = response.json()

    if data.get("errors"):
        raise RuntimeError(str(data["errors"]))

    return data.get("response", [])


async def get_team(name):
    teams = await api_get("teams", {"search": name})
    if not teams:
        return None
    return teams[0]["team"]



from datetime import datetime, timedelta, timezone

async def recent_stats(team_id):
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=60)

    season = today.year if today.month >= 7 else today.year - 1

    fixtures = await api_get(
        "fixtures",
        {
            "team": team_id,
            "season": season,
            "from": start_date.isoformat(),
            "to": today.isoformat(),
        }
    )

    finished = [
        item for item in fixtures
        if item["fixture"]["status"]["short"] in ("FT", "AET", "PEN")
    ]

    finished.sort(
        key=lambda item: item["fixture"]["date"],
        reverse=True
    )

    finished = finished[:10]

    goals_for = []
    goals_against = []

    for item in finished:
        teams = item["teams"]
        goals = item["goals"]

        if teams["home"]["id"] == team_id:
            goals_for.append(goals["home"] or 0)
            goals_against.append(goals["away"] or 0)
        else:
            goals_for.append(goals["away"] or 0)
            goals_against.append(goals["home"] or 0)

    if not goals_for:
        raise RuntimeError("No recent finished matches found")

    return (
        sum(goals_for) / len(goals_for),
        sum(goals_against) / len(goals_against),
        len(goals_for),
    )
    
    


def poisson(mean, goals):
    if mean <= 0:
        return 0
    return (mean ** goals) * math.exp(-mean) / math.factorial(goals)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚽ Welcome to GoalPredict AI!\n\n"
        "Commands:\n"
        "/team Arsenal\n"
        "/predict Arsenal vs Chelsea\n"
        "/help"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def team_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args)

    if not name:
        await update.message.reply_text("Use: /team Arsenal")
        return

    try:
        team = await get_team(name)
        if not team:
            await update.message.reply_text("Team not found.")
            return

        scored, conceded, games = await recent_stats(team["id"])

        await update.message.reply_text(
            f"⚽ {team['name']}\n"
            f"Recent matches analysed: {games}\n"
            f"Average goals scored: {scored:.2f}\n"
            f"Average goals conceded: {conceded:.2f}"
        )
    except Exception as exc:
        log.exception("Team command failed")
        await update.message.reply_text(f"Could not load team data: {exc}")


async def predict_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args)

    if " vs " not in text.lower():
        await update.message.reply_text(
            "Use: /predict Arsenal vs Chelsea"
        )
        return

    home_name, away_name = text.split(" vs ", 1)

    try:
        home = await get_team(home_name.strip())
        away = await get_team(away_name.strip())

        if not home or not away:
            await update.message.reply_text("One or both teams were not found.")
            return

        h_for, h_against, h_games = await recent_stats(home["id"])
        a_for, a_against, a_games = await recent_stats(away["id"])

        home_mean = max(0.1, (h_for + a_against) / 2)
        away_mean = max(0.1, (a_for + h_against) / 2)

        home_win = draw = away_win = over25 = btts = 0
        scores = []

        for hg in range(8):
            for ag in range(8):
                probability = (
                    poisson(home_mean, hg)
                    * poisson(away_mean, ag)
                )

                scores.append((probability, hg, ag))

                if hg > ag:
                    home_win += probability
                elif hg == ag:
                    draw += probability
                else:
                    away_win += probability

                if hg + ag > 2:
                    over25 += probability

                if hg > 0 and ag > 0:
                    btts += probability

        scores.sort(reverse=True)

        result = (
            f"⚽ {home['name']} vs {away['name']}\n\n"
            f"Home win: {home_win:.1%}\n"
            f"Draw: {draw:.1%}\n"
            f"Away win: {away_win:.1%}\n"
            f"Over 2.5 goals: {over25:.1%}\n"
            f"Both teams to score: {btts:.1%}\n\n"
            "Most likely scorelines:\n"
        )

        for probability, hg, ag in scores[:3]:
            result += f"{hg}-{ag} ({probability:.1%})\n"

        result += (
            f"\nRecent games used: {h_games} home-team, "
            f"{a_games} away-team.\n"
            "\nThis is a basic statistical estimate using recent goals, "
            "not a guarantee. It does not account for lineups, injuries, "
            "venue effects or opponent strength."
        )

        await update.message.reply_text(result)

    except Exception as exc:
        log.exception("Prediction failed")
        await update.message.reply_text(f"Prediction failed: {exc}")


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("team", team_command))
    app.add_handler(CommandHandler("predict", predict_command))

    log.info("GoalPredict AI is starting")
    app.run_polling()


