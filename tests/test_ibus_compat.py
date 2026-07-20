from app.ibus_compat import build_capability_flags


def test_build_capability_flags_supports_ibus_1_5_26_subset():
    class OldCapabilite:
        PREEDIT_TEXT = 1
        AUXILIARY_TEXT = 2
        LOOKUP_TABLE = 4
        FOCUS = 8
        PROPERTY = 16
        SURROUNDING_TEXT = 32

    assert build_capability_flags(OldCapabilite) == (
        (1, "preedit"),
        (2, "aux"),
        (4, "lookup"),
        (8, "focus"),
        (16, "property"),
        (32, "surrounding"),
    )


def test_build_capability_flags_includes_newer_optional_flags():
    class NewCapabilite:
        PREEDIT_TEXT = 1
        OSK = 64
        SYNC_PROCESS_KEY = 128

    assert build_capability_flags(NewCapabilite) == (
        (1, "preedit"),
        (64, "osk"),
        (128, "sync_key"),
    )
