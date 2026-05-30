"""Live jet-fuel price feed (keyless).

Jet-A doesn't trade on a free intraday public API, but its standard market proxy
is **ULSD / NY-Harbor heating oil futures** (the contract airlines hedge with).
We pull the front-month from stooq.com (no API key), add a small jet differential,
and expose $/gal. Falls back to crude (WTI) -> jet, then to a static default if
the network is unavailable. Cached with a short TTL so the UI can poll cheaply.

Result dict: {usd_per_gal, source, symbol, raw, as_of, live(bool)}
"""
import csv, io, time, urllib.request

STOOQ = "https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcvn&h&e=csv"
TTL_SEC = 300                 # refresh at most every 5 min
DEFAULT_USD_GAL = 2.50        # offline fallback
JET_OVER_ULSD = 0.18          # $/gal jet-A premium over ULSD (typical refining diff)
# crude -> jet very rough: ~42 gal/bbl, jet ~ crude/42 * (1 + crack); keep simple
CRUDE_TO_JET = lambda wti_bbl: wti_bbl / 42.0 * 1.7

_cache = {"t": 0.0, "val": None}


def _fetch_stooq(sym):
    url = STOOQ.format(sym=sym)
    with urllib.request.urlopen(url, timeout=6) as r:
        rows = list(csv.DictReader(io.StringIO(r.read().decode())))
    if not rows:
        return None
    row = rows[0]
    close = row.get("Close", "")
    if close in ("", "N/D"):
        return None
    return float(close), row.get("Date", ""), row.get("Time", ""), row.get("Name", sym)


def get_price(force=False):
    """Return current jet-fuel price dict, cached for TTL_SEC."""
    now = time.time()
    if not force and _cache["val"] and now - _cache["t"] < TTL_SEC:
        return _cache["val"]

    out = None
    # 1) ULSD / heating oil (best jet proxy), already $/gal
    try:
        r = _fetch_stooq("ho.f")
        if r:
            close, d, t, name = r
            out = {"usd_per_gal": round(close + JET_OVER_ULSD, 4),
                   "source": "stooq ULSD (NY Harbor) + jet diff",
                   "symbol": "HO.F", "raw": close,
                   "as_of": f"{d} {t} (exchange)", "live": True}
    except Exception:
        pass
    # 2) crude fallback ($/bbl -> $/gal jet)
    if out is None:
        try:
            r = _fetch_stooq("cl.f")
            if r:
                close, d, t, name = r
                out = {"usd_per_gal": round(CRUDE_TO_JET(close), 4),
                       "source": "stooq WTI crude -> jet (proxy)",
                       "symbol": "CL.F", "raw": close,
                       "as_of": f"{d} {t} (exchange)", "live": True}
        except Exception:
            pass
    # 3) offline default
    if out is None:
        out = {"usd_per_gal": DEFAULT_USD_GAL, "source": "offline default",
               "symbol": None, "raw": None, "as_of": "—", "live": False}

    _cache.update(t=now, val=out)
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(get_price(force=True), indent=2))
