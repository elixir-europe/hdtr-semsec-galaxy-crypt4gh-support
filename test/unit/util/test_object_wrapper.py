from galaxy.model.none_like import NoneDataset
from galaxy.security.object_wrapper import wrap_with_safe_string


class _DummyDatatypesRegistry:
    def get_datatype_by_extension(self, ext):
        del ext
        return object()


def test_wrap_with_safe_string_does_not_warn_for_nonedataset_and_preserves_wrapper_type(caplog):
    none_dataset = NoneDataset(datatypes_registry=_DummyDatatypesRegistry(), ext="txt.c4gh")

    with caplog.at_level("WARNING", logger="galaxy.security.object_wrapper"):
        wrapped = wrap_with_safe_string(none_dataset)

    assert "Unable to create dynamic subclass" not in caplog.text
    assert isinstance(wrapped, none_dataset.__class__)
