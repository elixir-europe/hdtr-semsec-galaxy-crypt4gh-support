from types import SimpleNamespace

from galaxy.jobs import JobWrapper
from galaxy.model import Dataset


def _dataset_assoc_with_instances(*instances):
    dataset = SimpleNamespace(id=42, history_associations=list(instances), library_associations=[])
    dataset_instance = SimpleNamespace(dataset=dataset)
    return SimpleNamespace(dataset=dataset_instance)


def test_normalize_successful_output_association_states_marks_pending_instances_ok():
    wrapper = JobWrapper.__new__(JobWrapper)
    added = []
    wrapper.sa_session = SimpleNamespace(add=lambda dataset_instance: added.append(dataset_instance))

    running_instance = SimpleNamespace(id=3, state=Dataset.states.RUNNING, dataset=SimpleNamespace(id=3))
    queued_instance = SimpleNamespace(id=4, state=Dataset.states.QUEUED, dataset=SimpleNamespace(id=4))
    ok_instance = SimpleNamespace(id=5, state=Dataset.states.OK, dataset=SimpleNamespace(id=5))
    association = _dataset_assoc_with_instances(running_instance, queued_instance, ok_instance)

    job = SimpleNamespace(id=9)
    wrapper._normalize_successful_output_association_states(job, [association])

    assert running_instance.state == Dataset.states.OK
    assert queued_instance.state == Dataset.states.OK
    assert ok_instance.state == Dataset.states.OK
    assert added == [running_instance, queued_instance]


def test_normalize_successful_output_association_states_leaves_non_pending_unchanged():
    wrapper = JobWrapper.__new__(JobWrapper)
    added = []
    wrapper.sa_session = SimpleNamespace(add=lambda dataset_instance: added.append(dataset_instance))

    ok_instance = SimpleNamespace(id=11, state=Dataset.states.OK, dataset=SimpleNamespace(id=11))
    failed_meta_instance = SimpleNamespace(
        id=12,
        state=Dataset.states.FAILED_METADATA,
        dataset=SimpleNamespace(id=12),
    )
    association = _dataset_assoc_with_instances(ok_instance, failed_meta_instance)

    job = SimpleNamespace(id=12)
    wrapper._normalize_successful_output_association_states(job, [association])

    assert ok_instance.state == Dataset.states.OK
    assert failed_meta_instance.state == Dataset.states.FAILED_METADATA
    assert added == []
