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
        self.finalization_context = None

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

    def crypt4gh_output_finalization_context(self):
        return self.finalization_context


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


def test_collect_primary_datasets_finalizes_assigned_primary_when_crypt4gh_context_present(monkeypatch, tmp_path):
    discovered = _DiscoveredFile(str(tmp_path / "sample1.report.tsv"))

    def _fake_discover_files(*_args, **_kwargs):
        yield discovered

    calls = []

    def _fake_finalize_about_to_persist_crypt4gh_payload(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("galaxy.job_execution.output_collect.discover_files", _fake_discover_files)
    monkeypatch.setattr(
        "galaxy.tools.crypt4gh_remote_execution.finalize_about_to_persist_crypt4gh_payload",
        _fake_finalize_about_to_persist_crypt4gh_payload,
    )

    job_context = _JobContext()
    job_context.job_working_directory = str(tmp_path)
    job_context.finalization_context = {
        "reencryption_service_url": "http://localhost:8000",
        "compute_public_key": "-----BEGIN CRYPT4GH PUBLIC KEY-----\nabc\n-----END CRYPT4GH PUBLIC KEY-----\n",
        "compute_keypair_id": "key-1",
        "compute_keypair_expiration_date": "",
    }

    outdata = _OutData()
    outdata.dataset = _DatasetCarrier(path="")
    outdata.dataset.id = 42
    outdata.dataset.purged = True
    outdata.dataset.external_filename = None
    outdata.dataset.get_file_name = lambda sync_cache=False: str(tmp_path / "dataset_42.dat")

    collect_primary_datasets(job_context, {"sample": outdata}, input_ext="tabular")

    assert len(calls) == 1
    call = calls[0]
    assert call["output_path"] == str(tmp_path / "sample1.report.tsv")
    assert call["encrypted_ext"] == "tabular.c4gh"
    assert call["encrypted_marker_path"].endswith("_c4gh_stage/outputs/ds_42.encrypted")
