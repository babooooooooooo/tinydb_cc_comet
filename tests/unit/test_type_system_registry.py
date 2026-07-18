from tinydb.type_system import lookup, codec_for, REGISTRY


def test_registry_has_15_core_types():
    expected = {"INT", "SMALLINT", "BIGINT", "FLOAT", "DOUBLE", "REAL",
                "TEXT", "VARCHAR", "CHAR", "BOOL", "BOOLEAN",
                "DECIMAL", "DATE", "TIME", "TIMESTAMP"}
    assert set(REGISTRY.keys()) == expected


def test_lookup_returns_codec():
    codec = lookup("INT")
    assert codec is not None
    assert hasattr(codec, "encode_py")
    assert hasattr(codec, "decode_bytes")


def test_codec_for_non_parametric_returns_singleton():
    a = codec_for("INT")
    b = codec_for("INT")
    assert a is b


def test_codec_for_varchar_creates_configured_instance():
    codec = codec_for("VARCHAR", (64,))
    assert codec is not None


def test_codec_for_varchar_without_params_raises():
    import pytest
    with pytest.raises(ValueError, match="VARCHAR requires"):
        codec_for("VARCHAR")


def test_codec_for_decimal_requires_two_params():
    import pytest
    with pytest.raises(ValueError, match="DECIMAL requires"):
        codec_for("DECIMAL")
    with pytest.raises(ValueError, match="DECIMAL requires"):
        codec_for("DECIMAL", (10,))


def test_codec_for_decimal_validates_p_s_bounds():
    import pytest
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (0, 0))
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (19, 0))
    with pytest.raises(ValueError, match="DECIMAL"):
        codec_for("DECIMAL", (10, 10))


def test_lookup_unknown_type_raises():
    import pytest
    with pytest.raises(KeyError):
        lookup("UNKNOWN_TYPE")