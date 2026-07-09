from __future__ import annotations

from openskill.models import PlackettLuce
from openskill.models.weng_lin.plackett_luce import PlackettLuceRating

from game_of_agents.models import (
    BASE_TRUESKILL_MU,
    BASE_TRUESKILL_SIGMA,
    BotSubmission,
    LeaderboardScore,
    RatingConfig,
    display_rating,
)


def build_openskill_model(config: RatingConfig) -> PlackettLuce:
    return PlackettLuce(
        mu=BASE_TRUESKILL_MU,
        sigma=BASE_TRUESKILL_SIGMA,
        beta=config.beta,
        tau=config.tau,
    )


def conservative_score(mu: float, sigma: float) -> float:
    return mu - 3 * sigma


def rating_score(mu: float, sigma: float, mode: LeaderboardScore) -> float:
    if mode == LeaderboardScore.MU:
        return mu
    return conservative_score(mu, sigma)


def create_rating(model: PlackettLuce, mu: float, sigma: float) -> PlackettLuceRating:
    return model.rating(mu=mu, sigma=sigma)


def update_bot_rating(bot: BotSubmission, config: RatingConfig, rating: PlackettLuceRating) -> None:
    bot.rating_mu = float(rating.mu)
    bot.rating_sigma = float(rating.sigma)
    bot.rating_score = rating_score(bot.rating_mu, bot.rating_sigma, config.leaderboard_score)
    bot.elo = display_rating(bot.rating_score)
