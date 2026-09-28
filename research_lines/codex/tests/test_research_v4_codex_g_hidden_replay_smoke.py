"""CPU-only unit tests; no original dataset or model loaded."""
import pytest
import torch

from research_v4.codex_g_hidden_replay_smoke import (
    DATA, OUT, ROOT, TRACE_DIRS, Delta, HiddenCapture, ReplayGuard, RouteDelta,
)


def test_delta_exact_and_error():
    delta = Delta()
    delta.add(torch.tensor([1., 2.]), torch.tensor([1., 3.]))
    result = delta.result()
    assert result["elements"] == 2 and result["unequal_elements"] == 1
    assert result["max_abs"] == 1 and result["mean_abs"] == 0.5
    assert result["relative_l2"] == pytest.approx((1 / 5) ** 0.5)
    assert result["bitwise_equal"] is False


def test_delta_signed_zero_is_not_bitwise_equal():
    delta = Delta()
    delta.add(torch.tensor([0.]), torch.tensor([-0.]))
    assert delta.result()["unequal_elements"] == 0
    assert delta.result()["bitwise_equal"] is False


def test_delta_rejects_shapes_and_nonfinite():
    with pytest.raises(ValueError):
        Delta().add(torch.zeros(2), torch.zeros(3))
    with pytest.raises(ValueError):
        Delta().add(torch.zeros(1), torch.tensor([float("nan")]))


def route(logits, ids):
    logits = torch.tensor(logits, dtype=torch.bfloat16).reshape(1, 1, -1)
    ids = torch.tensor(ids, dtype=torch.int16).reshape(1, 1, 4)
    return dict(router_logits=logits, top_k_ids=ids,
                top_k_weights=logits.gather(-1, ids.long()).softmax(-1))


def test_route_exact_and_tie_accounting():
    a = route([3, 2, 1, 0, 0], [0, 1, 2, 3])
    b = route([3, 2, 1, 0, 0], [0, 1, 2, 4])
    exact = RouteDelta()
    exact.add(a, a)
    assert exact.exact()
    tied = RouteDelta()
    tied.add(a, b)
    assert not tied.exact()
    assert tied.result()["changed_sets_explained_by_exact_ties"] == 1
    assert tied.result()["changed_sets_not_explained_by_exact_ties"] == 0


def test_route_real_support_change_not_called_tie():
    delta = RouteDelta()
    delta.add(route([4, 3, 2, 1, 0], [0, 1, 2, 3]), route([4, 3, 2, 0, 1], [0, 1, 2, 4]))
    assert delta.result()["changed_sets_not_explained_by_exact_ties"] == 1


def test_guard_rejects_sealed_labels_other_samples_and_prior_results():
    guard = ReplayGuard(ROOT)
    for path in (DATA / "g_conf/trace.json", DATA / "annotations/g_dev/final_unblinded.jsonl",
                 DATA / "g_dev/core_72_cells/g-dev-002/attack/trace.json", OUT.parent / "mechanism_m15_token_return_v1/run_manifest.json"):
        with pytest.raises(PermissionError):
            guard.check_path(path)
    assert guard.check_path(TRACE_DIRS[0] / "trace.json") == (TRACE_DIRS[0] / "trace.json").resolve()
    assert guard.check_path(OUT / "result.json") == (OUT / "result.json").resolve()


def test_hidden_hooks_do_not_mutate_and_keep_last_prompt_token():
    class Layer(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.mlp = torch.nn.Identity()

        def forward(self, value):
            return self.mlp(value) + 1

    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.model = torch.nn.Module()
            self.model.layers = torch.nn.ModuleList([Layer() for _ in range(24)])
            self.model.norm = torch.nn.Identity()

        def forward(self, value):
            for layer in self.model.layers:
                value = layer(value)
            return self.model.norm(value)

    model = Model()
    value = torch.arange(15.).reshape(1, 3, 5)
    expected = model(value)
    with HiddenCapture(model) as capture:
        actual = model(value)
        tensors = capture.take()
        assert torch.equal(actual, expected)
        assert tensors["router_input_hidden"].shape == (24, 1, 5)
        assert torch.equal(tensors["router_input_hidden"][0:1], value[:, -1:])
        assert torch.equal(tensors["block_output_hidden"][-1:], expected[:, -1:])
        assert torch.equal(tensors["final_norm_hidden"], expected[:, -1:])
    assert not capture.handles
