from __future__ import annotations

from game_diag.collectors import gpu as gpu_module


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class FakePdh:
    def __init__(self) -> None:
        self.collect_statuses = [0x800007D5, 0]
        self.open_count = 0
        self.PdhOpenQueryW = FakeFunction(self._open)
        self.PdhAddEnglishCounterW = FakeFunction(self._add_counter)
        self.PdhCollectQueryData = FakeFunction(self._collect)
        self.PdhGetFormattedCounterArrayW = FakeFunction(lambda *_args: 0)
        self.PdhCloseQuery = FakeFunction(lambda _query: 0)

    def _open(self, _source, _user_data, query_pointer):
        self.open_count += 1
        query_pointer._obj.value = 101
        return 0

    @staticmethod
    def _add_counter(_query, _path, _user_data, counter_pointer):
        counter_pointer._obj.value = 202
        return 0

    def _collect(self, _query):
        return self.collect_statuses.pop(0)


def test_process_gpu_retries_when_engine_instances_are_not_ready(monkeypatch):
    fake_pdh = FakePdh()
    monkeypatch.setattr(gpu_module.ctypes, "WinDLL", lambda *_args, **_kwargs: fake_pdh)

    sampler = gpu_module.ProcessGpuSampler(21652, retry_interval_sec=2.0)

    assert not sampler.available
    assert "0x800007D5" in (sampler.reason or "")
    assert "自动重试" in (sampler.reason or "")
    sampler._next_retry_at = 0.0
    assert sampler._ensure_available()
    assert sampler.available
    assert sampler.reason is None
    assert fake_pdh.open_count == 2


def test_pdh_status_is_formatted_as_unsigned_hex():
    assert gpu_module.ProcessGpuSampler._status_code(-2147481643) == 0x800007D5
