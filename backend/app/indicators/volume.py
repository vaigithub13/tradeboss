"""VWAP, resetting every IST calendar day."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.basic import source_series

IST_OFFSET_S = 19_800
DAY_S = 86_400


class VolumeRequired(ValueError):
    """Raised instead of computing a volume-weighted indicator from zero volume."""


def vwap(df: pd.DataFrame, source: str = "hlc3") -> pd.Series:
    """cumulative(src * volume) / cumulative(volume), reset at each IST midnight.

    NaN until the day's cumulative volume is > 0. Zero-volume bars carry the running value.
    Raises VolumeRequired when there is no volume at all (e.g. index data such as Nifty).
    """
    volume = df["volume"].to_numpy(dtype=float)
    if len(volume) == 0 or not np.nansum(volume) > 0:
        raise VolumeRequired("VWAP needs volume, but this data has none (all volume is 0)")
    day = (df["time"].to_numpy(dtype="int64") + IST_OFFSET_S) // DAY_S
    src = source_series(df, source).to_numpy(dtype=float)
    frame = pd.DataFrame({"day": day, "pv": src * volume, "v": volume})
    cum = frame.groupby("day", sort=False)[["pv", "v"]].cumsum()
    cum_v = cum["v"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(cum_v > 0, cum["pv"].to_numpy() / cum_v, np.nan)
    return pd.Series(out, index=df.index)
