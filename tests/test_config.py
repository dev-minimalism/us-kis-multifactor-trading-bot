# test_config.py - 설정 무결성
from multifactor_bot import config


def test_factor_weights_sum_to_one():
  assert abs(sum(config.FACTOR_WEIGHTS.values()) - 1.0) < 1e-9


def test_factor_weights_have_all_four_factors():
  assert set(config.FACTOR_WEIGHTS) == {'momentum', 'value', 'quality', 'volatility'}


def test_account_ids_are_distinct_ints():
  assert isinstance(config.PAPER_ACCOUNT_ID, int)
  assert isinstance(config.LIVE_ACCOUNT_ID, int)
  assert config.PAPER_ACCOUNT_ID != config.LIVE_ACCOUNT_ID


def test_cash_flow_threshold_is_positive():
  assert config.LIVE_CASH_FLOW_THRESHOLD_USD > 0


def test_risk_thresholds_are_negative_percentages():
  assert config.STOP_LOSS_PERCENT < 0
  assert config.TRAILING_STOP_PERCENT < 0
  assert 0 <= config.MIN_HOLD_DAYS < config.MAX_HOLD_DAYS


def test_market_sessions_have_valid_times():
  for name, times in config.MARKET_SESSIONS.items():
    for key in ('start', 'end'):
      h, m = map(int, times[key].split(':'))
      assert 0 <= h < 24 and 0 <= m < 60, f"{name}.{key}"
