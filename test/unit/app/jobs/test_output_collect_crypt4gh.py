from galaxy.job_execution.output_collect import collect_primary_datasets
from galaxy.model import Dataset


class _DatasetCarrier:
    def __init__(self, path: str):
        self.path = path


class _OutData:
    class states:
        OK = "ok"
        ERROR = "error"
        DEFERRED = "deferred"
        FAILED_METADATA = "failed_metadata"

    def __init__(self):
        self.name = "sample"
        self.ext = "tabular"
        self.dataset = Dataset()
        self.dataset.purged = True
        self.dbkey = "?"
        self.designation = None
        self.state = self.states.SETTING_METADATA if hasattr(self.states, "SETTING_METADATA") else "setting_metadata"
        self.init_meta_calls = 0
        self.set_meta_calls = 0
        self.set_peek_calls = 0
        self.discovered = False

    def change_datatype(self, ext: str):
        self.ext = ext

    def init_meta(self):
        self.init_meta_calls += 1

    def set_meta(self):
        self.set_meta_calls += 1

    def set_peek(self):
        self.set_peek_calls += 1


class _Collector:
    assign_primary_output = True


class _Match:
    def __init__(self):
        self.designation = "sample1"
        self.ext = "tabular"
        self.dbkey = "?"
        self.visible = True
        self.name = "sample"
        self.link_data = False
        self.sources = []
        self.hashes = []
        self.created_from_basename = None


class _DiscoveredFile:
    def __init__(self, path: str):
        self.path = path
        self.collector = _Collector()
        self.match = _Match()


class _JobContext:
    def __init__(self):
        self.job_working_directory = "/tmp"
        self.final_job_state = "ok"
        self.sa_session = None
        self.tool_provided_metadata = self

    def output_def(self, _name):
        return None

    def increment_discovered_file_count(self):
        return

    def _resolve_discovered_crypt4gh_extension(self, ext: str) -> str:
        return f"{ext}.c4gh"

    def get_dataset_finish_context(self, *_args, **_kwargs):
        return {}

    def get_new_dataset_meta_by_basename(self, *_args, **_kwargs):
        return {}

    def create_dataset(self, *args, **kwargs):
        raise AssertionError("no secondary discovered datasets expected in this test")

    def add_output_dataset_association(self, *_args, **_kwargs):
        return

    def add_datasets_to_history(self, *_args, **_kwargs):
        return

    def get_job_id(self):
        return 1


def test_collect_primary_datasets_sets_primary_state_ok_when_assigning_primary_output(monkeypatch):
    discovered = _DiscoveredFile("/tmp/sample1.report.tsv")

    def _fake_discover_files(*_args, **_kwargs):
        yield discovered

    monkeypatch.setattr("galaxy.job_execution.output_collect.discover_files", _fake_discover_files)

    job_context = _JobContext()
    outdata = _OutData()
    outdata.state = "setting_metadata"

    collect_primary_datasets(job_context, {"sample": outdata}, input_ext="tabular")

    assert outdata.state == outdata.states.OK
