from types import SimpleNamespace

from galaxy.tools.crypt4gh_remote_execution import _resolve_base_output_extension


def test_resolve_base_output_extension_strips_crypt4gh_suffix():
    dataset = SimpleNamespace(ext="fastqsanger.c4gh")
    tool_output = SimpleNamespace(format="fastqsanger")

    assert _resolve_base_output_extension(dataset=dataset, tool_output=tool_output) == "fastqsanger"


def test_resolve_base_output_extension_keeps_plain_extension():
    dataset = SimpleNamespace(ext="fastqsanger")
    tool_output = SimpleNamespace(format="ignored")

    assert _resolve_base_output_extension(dataset=dataset, tool_output=tool_output) == "fastqsanger"


def test_resolve_base_output_extension_returns_none_for_empty_extension():
    dataset = SimpleNamespace(ext="")
    tool_output = SimpleNamespace(format="fastqsanger")

    assert _resolve_base_output_extension(dataset=dataset, tool_output=tool_output) is None


def test_resolve_base_output_extension_uses_declared_format_for_auto_extensions():
    dataset = SimpleNamespace(ext="auto")
    tool_output = SimpleNamespace(format="fastqsanger")

    assert _resolve_base_output_extension(dataset=dataset, tool_output=tool_output) == "fastqsanger"
