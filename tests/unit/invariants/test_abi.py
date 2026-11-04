"""The C interface a port keeps (``c_abi_kept``, D-56)."""

from __future__ import annotations

from codecoremorph.invariants.abi import MAX_SHOWN, c_abi_differences, c_abi_feedback

HEADER = {
    "tk_ring_new": "struct tk_ring *(unsigned long long)",
    "tk_ring_len": "unsigned long long (const struct tk_ring *)",
    "tk_checksum": "unsigned int (const unsigned char *, unsigned long long)",
}


def test_the_same_interface_passes() -> None:
    exported = {**HEADER, "tk_ring_len": "unsigned long long  (const struct tk_ring *)"}
    exported["tk_private_helper"] = "void (void)"  # exports the header doesn't declare are fine
    assert c_abi_differences(HEADER, exported) == []
    assert c_abi_feedback("port", ["include/ring.h"], []) == ""


def test_missing_and_changed_functions() -> None:
    exported = {
        "tk_ring_new": "struct tk_ring *(unsigned long long)",
        "tk_ring_len": "unsigned long long (struct tk_ring *)",  # lost its const
    }
    problems = c_abi_differences(HEADER, exported)
    assert problems == [
        "`tk_checksum` isn't exported; the header declares "
        "`unsigned int (const unsigned char *, unsigned long long)`",
        "`tk_ring_len` is exported as `unsigned long long (struct tk_ring *)`, but the header "
        "declares `unsigned long long (const struct tk_ring *)`",
    ]
    text = c_abi_feedback("port_c_abi_module", ["include/ring.h"], problems)
    assert text.startswith("port_c_abi_module: the new code must export the C interface of ")
    assert "- `tk_checksum` isn't exported" in text


def test_feedback_is_capped() -> None:
    problems = [f"`f{i}` isn't exported" for i in range(MAX_SHOWN + 5)]
    text = c_abi_feedback("port", ["a.h"], problems)
    assert text.endswith("- … and 5 more")
