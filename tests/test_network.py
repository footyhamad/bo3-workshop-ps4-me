from bo3wd.network import NetworkSpeedMeter


def test_network_meter_reports_rx_and_tx_rates_over_the_sample_window():
    readings = iter([
        {"wifi": (1_000, 2_000), "vpn": (400, 500)},
        {"wifi": (3_000, 5_000), "vpn": (900, 700)},
    ])
    clock_values = iter([10.0, 12.0])
    meter = NetworkSpeedMeter(reader=lambda: next(readings), clock=lambda: next(clock_values))

    assert meter.sample() == (0.0, 0.0)
    receive, send = meter.sample()

    assert receive == 1_250.0
    assert send == 1_600.0


def test_network_meter_ignores_new_interfaces_until_the_next_sample():
    readings = iter([
        {"wifi": (10, 20)},
        {"wifi": (110, 220), "new-adapter": (999_999, 999_999)},
    ])
    clock_values = iter([1.0, 2.0])
    meter = NetworkSpeedMeter(reader=lambda: next(readings), clock=lambda: next(clock_values))
    meter.sample()

    assert meter.sample() == (100.0, 200.0)
