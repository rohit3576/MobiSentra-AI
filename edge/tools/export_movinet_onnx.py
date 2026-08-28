#!/usr/bin/env python3
"""Export the engares MoViNet A2 checkpoint to an explicit-states ONNX graph.

Phase 5 Step 5.1c artifact producer. Uses the Step 5.1b winning recipe
(flatten the streaming state dict into the graph signature; runtime feeds
states back per call). The edge package consumes the .onnx through
``vision/action.py`` + onnxruntime — TensorFlow never enters the edge env;
this tool runs OFFLINE in the spike venv:

    <spike-venv>/bin/python edge/tools/export_movinet_onnx.py
        [--ckpt mlops/datasets/movinet/movinet_a2_5fps_32bs_0.001lr_0.3dr_0tl]
        [--out mlops/datasets/movinet/movinet_a2_explicit_states.onnx]

Writes the .onnx plus a provenance sidecar JSON (variant, source checkpoint,
converter versions, SHA256, I/O signature). Benchmark-only posture: see
mlops/datasets/SOURCES.md — never committed, never redistributed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from pathlib import Path

RES = 224
CKPT_DEFAULT = "mlops/datasets/movinet/movinet_a2_5fps_32bs_0.001lr_0.3dr_0tl"
OUT_DEFAULT = "mlops/datasets/movinet/movinet_a2_explicit_states.onnx"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 16):
            digest.update(chunk)
    return digest.hexdigest()


def build_external_model(ckpt_dir: Path):
    import tensorflow as tf
    from official.projects.movinet.modeling import movinet, movinet_model

    backbone = movinet.Movinet(
        model_id="a2",
        causal=True,
        conv_type="2plus1d",
        se_type="2plus3d",
        activation="hard_swish",
        gating_activation="hard_sigmoid",
        use_positional_encoding=False,
        use_external_states=True,
    )
    model = movinet_model.MovinetClassifier(backbone, num_classes=2, output_states=True)
    model.build([1, 1, RES, RES, 3])
    ckpt = tf.train.latest_checkpoint(str(ckpt_dir))
    if ckpt is None:
        raise SystemExit(f"no checkpoint found in {ckpt_dir}")
    model.load_weights(ckpt).expect_partial()
    return model, ckpt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckpt", type=Path, default=Path(CKPT_DEFAULT))
    parser.add_argument("--out", type=Path, default=Path(OUT_DEFAULT))
    args = parser.parse_args(argv)

    import tensorflow as tf
    import tf2onnx

    model, ckpt = build_external_model(args.ckpt)
    states = model.init_states([1, 1, RES, RES, 3])
    state_keys = list(states.keys())

    @tf.function
    def step(image, *flat_states):
        states_dict = dict(zip(state_keys, flat_states, strict=True))
        logits, new_states = model({**states_dict, "image": image})
        return (logits, *[new_states[k] for k in state_keys])

    image_spec = tf.TensorSpec([1, 1, RES, RES, 3], tf.float32, name="image")
    state_specs = [
        tf.TensorSpec(states[k].shape, states[k].dtype, name=f"state_{i}")
        for i, k in enumerate(state_keys)
    ]
    proto, _ = tf2onnx.convert.from_function(step, input_signature=[image_spec, *state_specs])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(proto.SerializeToString())
    n_states = len(state_specs)
    print(f"[ok] {args.out.name}: {args.out.stat().st_size / 1e6:.1f} MB, {n_states} state tensors")

    sidecar = {
        "date": time.strftime("%Y-%m-%d"),
        "variant": args.ckpt.name,
        "source_checkpoint": str(ckpt),
        "onnx_sha256": sha256_of(args.out),
        "onnx_bytes": args.out.stat().st_size,
        "converters": {
            "tensorflow": tf.__version__,
            "tf2onnx": tf2onnx.__version__,
            "platform": platform.platform(),
        },
        "io": {
            "image_input": "image",
            "shape": [1, 1, RES, RES, 3],
            "state_inputs": n_states,
            "outputs": ["logits", *[f"state_{i}" for i in range(n_states)]],
            "logit_order": ["Fight", "No_Fight"],
        },
        "posture": "benchmark-only; engares repo unlicensed (wrapper-only); "
        "RWF-2000-derived weights — see mlops/datasets/SOURCES.md",
    }
    sidecar_path = args.out.with_suffix(".onnx.provenance.json")
    sidecar_path.write_text(json.dumps(sidecar, indent=2))
    print(f"[ok] {sidecar_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
