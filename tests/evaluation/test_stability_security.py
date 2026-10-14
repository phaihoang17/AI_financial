from types import SimpleNamespace

import pytest

from src.evaluation.run_security_suite import Canaries, _effects, probes
from src.evaluation.run_soak_eval import slope, trend_analysis


def test_slope_fits_a_line():
    assert slope([(0, 1), (1, 3), (2, 5)]) == pytest.approx(2)
    assert slope([(0, 1), (1, 1)]) is None


def test_trend_analysis_flags_growth_and_ignores_warmup():
    flat = [{"t_s": t * 60, "rss_mb": 130 + (40 if t == 0 else 0), "fds": 20, "threads": 10, "temp_dirs": 0}
            for t in range(31)]
    assert all(trend_analysis(flat, 0.1)["checks"].values())
    leaking = [{"t_s": t * 60, "rss_mb": 130 + 5 * t, "fds": 20 + t, "threads": 10, "temp_dirs": 0} for t in range(31)]
    checks = trend_analysis(leaking, 0.1)["checks"]
    assert not checks["rss_growth_within_budget"] and not checks["descriptors_flat"] and checks["threads_flat"]


def test_canaries_observe_disclosure_files_and_network(tmp_path):
    import socket

    canaries = Canaries(tmp_path)
    try:
        assert set(probes(canaries)) >= {"open_secret", "pandas_read_url", "pandas_os_system", "memory_bomb"}
        clean = SimpleNamespace(result={"value": 1}, evidence=None)
        assert _effects(canaries, clean, canaries.connections) == []
        leaked = SimpleNamespace(result={"value": canaries.secret_value}, evidence=None)
        canaries.write_target.write_text("x")
        before = canaries.connections
        with socket.create_connection(("127.0.0.1", canaries.port), timeout=2):
            pass
        for _ in range(50):
            if canaries.connections > before:
                break
            import time
            time.sleep(0.05)
        assert set(_effects(canaries, leaked, before)) == {"SECRET_DISCLOSED", "FILE_WRITTEN", "NETWORK_REACHED"}
    finally:
        canaries.close()


def test_latency_drift_compares_pooled_windows_per_worker():
    from src.evaluation.run_soak_eval import latency_drift

    def window(t, m7, t2p):
        return {"t_s": t, "latencies": {"m7": m7, "t2p": t2p}, "m7_p95_ms": max(m7), "t2p_p95_ms": max(t2p)}

    steady = [window(i * 15, [100, 110], [500, 520]) for i in range(40)]
    assert all(item["ratio"] <= 1.5 for item in latency_drift(steady, 0.1).values())
    # A first window holding only fast M7 completions no longer skews the comparison.
    skewed = [window(0, [90], [300])] + steady
    assert latency_drift(skewed, 0.1)["t2p"]["ratio"] <= 1.5
    degrading = [window(i * 15, [100 + 10 * i], [500 + 50 * i]) for i in range(40)]
    assert latency_drift(degrading, 0.1)["t2p"]["ratio"] > 1.5


def test_quiescent_checkpoints_must_not_grow():
    from src.evaluation.run_soak_eval import quiescent_trend

    def point(t, fds, threads=20):
        return {"t_s": t, "fds": fds, "threads": threads, "temp_dirs": 0, "workers": 0}

    steady = quiescent_trend([point(300, 30), point(600, 30), point(900, 29), point(1800, 28, threads=4)])
    assert all(steady["checks"].values())
    leaking = quiescent_trend([point(300, 30), point(600, 31), point(900, 32)])
    assert not leaking["checks"]["quiescent_fds_flat"] and leaking["checks"]["quiescent_threads_flat"]
    assert not quiescent_trend([point(300, 30)])["checks"]["quiescent_fds_flat"]  # too few checkpoints
