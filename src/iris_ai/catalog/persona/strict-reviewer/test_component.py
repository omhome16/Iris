def test_component():
    import component

    assert "Verdict:" in component.Component().text()
