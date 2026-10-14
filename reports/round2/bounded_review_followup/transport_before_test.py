from btc_microstructure import bounded_transport


def test_bounded_transport_has_physical_test_only_adapter() -> None:
    assert hasattr(bounded_transport, "AioBoundedTransport")
