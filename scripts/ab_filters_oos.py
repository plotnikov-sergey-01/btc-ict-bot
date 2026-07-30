from copy import deepcopy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ict_bot.config import load_config
from ict_bot.runner import load_market_data, run_backtest

cfg = load_config()
df_15m, df_1h, df_4h, df_1d, funding = load_market_data(cfg)

sets = {
    "none": (False, False, False),
    "vol": (True, False, False),
    "vol+disp": (True, True, False),
    "all": (True, True, True),
}

for name, (vol, disp, pd_) in sets.items():
    c = deepcopy(cfg)
    c["filters"]["volatility"]["enabled"] = vol
    c["filters"]["displacement"]["enabled"] = disp
    c["filters"]["premium_discount"]["enabled"] = pd_
    _, _, m, s = run_backtest(c, df_15m, df_1h, df_4h, df_1d, funding, sample_mode="out_of_sample")
    print(
        f"{name:10} sig={len(s):4} trades={m['trades']:3} "
        f"avgR/mo={m['avg_r_per_month']:6.2f} pct5R={m['pct_months_at_goal']:.2f} "
        f"ret={m['total_return_pct']:.1f}%"
    )
