"""Repository classes — one per table."""

from database.repositories.api_usage_repository import ApiUsageRepository
from database.repositories.backtest_stats import BacktestStatsRepository
from database.repositories.recommendations import RecommendationRepository
from database.repositories.cycles import CycleRepository
from database.repositories.daily_summaries import DailySummaryRepository
from database.repositories.decisions import DecisionRepository
from database.repositories.decision_scores_repository import DecisionScoresRepository
from database.repositories.judge_spot_checks_repository import JudgeSpotChecksRepository
from database.repositories.liquidity import LiquidityRepository
from database.repositories.monthly_evaluations_repository import MonthlyEvaluationsRepository
from database.repositories.outcome_repository import OutcomeRepository
from database.repositories.scorecard_repository import ScorecardRepository
from database.repositories.strategy_health import StrategyHealthRepository
from database.repositories.strategy_states import StrategyStateRepository
from database.repositories.token_usage_repository import TokenUsageRepository
from database.repositories.trades import TradeRepository

__all__ = [
    "ApiUsageRepository",
    "BacktestStatsRepository",
    "RecommendationRepository",
    "CycleRepository",
    "DailySummaryRepository",
    "DecisionRepository",
    "DecisionScoresRepository",
    "JudgeSpotChecksRepository",
    "LiquidityRepository",
    "MonthlyEvaluationsRepository",
    "OutcomeRepository",
    "ScorecardRepository",
    "StrategyHealthRepository",
    "StrategyStateRepository",
    "TokenUsageRepository",
    "TradeRepository",
]
