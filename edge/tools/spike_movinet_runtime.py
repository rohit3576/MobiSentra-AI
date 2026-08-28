#!/usr/bin/env python3
"""Phase 5 Step 5.1b — MoViNet A2 runtime spike: TF vs TFLite vs ONNX.

Measures per-frame streaming-inference latency of the engares A2 violence
checkpoint (loaded through Google's Apache-2.0 tf-models-official code —
wrapper-only posture, zero engares code) on this laptop, plus a TFLite
conversion (float32 + dynamic-range int8) and an ONNX attempt with a
streaming-semantics check.

RUNTIME ENV — throwaway venv, NOT the edge env (TF is spike-only until a
runtime is chosen):
    python3 -m venv .venv
    .venv/bin/pip install tensorflow tf-models-official opencv-python \
        tf2onnx onnxruntime
    .venv/bin/python edge/tools/spike_movinet_runtime.py

Outputs: printed table + JSON to ``edge/runs/movinet-spike.json``.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

import cv2
import numpy as np

RES = 224  # A2 native resolution (engares model_specs)
CKPT_DEFAULT = (
    "mlops/datasets/movinet/movinet_a2_5fps_32bs_0.001lr_0.3dr_0tl"
)


def letterbox(frame_bgr: np.ndarray, size: int) -> np.ndarray:
    """cv2 mirror of tf.image.resize_with_pad: aspect-preserving resize,
    centered pad, RGB, float32 /255 (engares preprocessing recipe)."""
    h, w = frame_bgr.shape[:2]
    scale = size / max(h, w)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((size, size, 3), dtype=np.uint8)
    top, left = (size - nh) // 2, (size - nw) // 2
    canvas[top : top + nh, left : left + nw] = resized
    return canvas[..., ::-1].astype(np.float32) / 255.0  # BGR -> RGB


def sample_frames(video: Path, fps: float, seconds: float) -> tuple[list[np.ndarray], float]:
    cap = cv2.VideoCapture(str(video))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(src_fps / fps)))
    frames: list[np.ndarray] = []
    prep_ms: list[float] = []
    while len(frames) < int(seconds * fps):
        ok, frame = cap.read()
        if not ok:
            break
        if len(frames) == 0 or (len(frames) * step) % step == 0:
            if cap.get(cv2.CAP_PROP_POS_FRAMES) % step != 0:
                continue
        t0 = time.perf_counter()
        frames.append(letterbox(frame, RES))
        prep_ms.append((time.perf_counter() - t0) * 1e3)
    cap.release()
    return frames, statistics.mean(prep_ms) if prep_ms else 0.0


def stats_ms(samples: list[float]) -> dict[str, float]:
    s = sorted(samples)
    return {
        "mean_ms": statistics.mean(s),
        "p50_ms": s[len(s) // 2],
        "p95_ms": s[int(len(s) * 0.95)],
        "max_ms": s[-1],
    }


def build_model(use_external_states: bool, ckpt_dir: Path):
    """Google tf-models-official architecture + engares checkpoint weights."""
    import tensorflow as tf
    from official.projects.movinet.modeling import movinet, movinet_model

    backbone = movinet.Movinet(
        model_id="a2",
        causal=True,
        conv_type="2plus1d",
        se_type="2plus3d",
        activation="hard_swish",
        gating_activation="hard_sigmoid",
        use_positional_encoding=False,  # a2 only; a3/a4/a5 use True
        use_external_states=use_external_states,
    )
    model = movinet_model.MovinetClassifier(
        backbone, num_classes=2, output_states=use_external_states
    )
    model.build([1, 1, RES, RES, 3])

    ckpt = tf.train.latest_checkpoint(str(ckpt_dir))
    if ckpt is None:
        raise SystemExit(f"no checkpoint found in {ckpt_dir}")
    loaded = 0
    try:
        model.load_weights(ckpt).expect_partial()
        loaded = len(model.weights)
    except Exception:
        reader = tf.train.load_checkpoint(ckpt)
        available = reader.get_variable_to_shape_map()
        for w in model.weights:
            name = w.name.removesuffix(":0")
            if name in available:
                w.assign(reader.get_tensor(name))
                loaded += 1
    n_matched, n_total = loaded, len(model.weights)
    print(f"[build] external_states={use_external_states} weights matched {n_matched}/{n_total}")
    if loaded < len(model.weights):
        raise SystemExit("weight load incomplete — refusing to measure a broken model")
    return model, ckpt


def measure_tf(model, frames: list[np.ndarray], warmup: int = 10) -> dict:
    import tensorflow as tf

    def step_eager(frames_batch: list[np.ndarray]) -> list[np.ndarray]:
        states = model.init_states([1, 1, RES, RES, 3])
        logits_out = []
        for fr in frames_batch:
            image = tf.convert_to_tensor(fr[np.newaxis, np.newaxis])
            logits, states = model({**states, "image": image})
            logits_out.append(logits.numpy()[0])
        return logits_out

    step_eager(frames[:warmup])  # warmup + graph build
    t0 = time.perf_counter()
    logits_all = step_eager(frames)
    eager_ms = (time.perf_counter() - t0) * 1e3 / max(1, len(frames))

    @tf.function
    def step_graph(image, states_dict):
        logits, new_states = model({**states_dict, "image": image})
        return logits, new_states

    states = model.init_states([1, 1, RES, RES, 3])
    for fr in frames[:warmup]:
        image = tf.convert_to_tensor(fr[np.newaxis, np.newaxis])
        _, states = step_graph(image, states)
    t0 = time.perf_counter()
    states = model.init_states([1, 1, RES, RES, 3])
    for fr in frames:
        image = tf.convert_to_tensor(fr[np.newaxis, np.newaxis])
        logits_g, states = step_graph(image, states)
    graph_ms = (time.perf_counter() - t0) * 1e3 / max(1, len(frames))

    return {
        "per_frame_wall_ms_eager": eager_ms,
        "per_frame_wall_ms_graph": graph_ms,
        "logits_last": [float(x) for x in logits_all[-1]],
    }


def convert_and_measure_tflite(model_internal, frames: list[np.ndarray]) -> dict:
    import tensorflow as tf

    results = {}
    for variant, opts in {
        "float32": [],
        "dr_int8": [tf.lite.Optimize.DEFAULT],
    }.items():
        converter = tf.lite.TFLiteConverter.from_keras_model(model_internal)
        converter.optimizations = opts
        # MoViNet 2plus1d decomposition hits tf.DepthwiseConv2dNative, which
        # needs flex ops on TF 2.20 (round 1: builtin-only failed to translate)
        converter.target_spec.supported_ops = [
            tf.lite.OpsSet.TFLITE_BUILTINS,
            tf.lite.OpsSet.SELECT_TF_OPS,
        ]
        try:
            buf = converter.convert()
        except Exception as exc:
            results[variant] = {"error": f"convert failed: {exc}"}
            continue
        try:
            interp = tf.lite.Interpreter(model_content=buf)
            interp.allocate_tensors()
            image_detail = next(d for d in interp.get_input_details() if d["name"] == "image")
            interp.set_tensor(
                image_detail["index"],
                frames[0][np.newaxis, np.newaxis].astype(image_detail["dtype"]),
            )
            interp.invoke()
        except Exception as exc:
            results[variant] = {
                "error": f"interpreter failed (flex delegate unavailable on macOS): {exc}"
            }
            continue
        # reachable only where the flex delegate exists (Jetson/Linux builds)
        t0 = time.perf_counter()
        for fr in frames:
            interp.set_tensor(
                image_detail["index"], fr[np.newaxis, np.newaxis].astype(image_detail["dtype"])
            )
            interp.invoke()
        wall_ms = (time.perf_counter() - t0) * 1e3 / max(1, len(frames))
        results[variant] = {
            "per_frame_wall_ms": wall_ms,
            "size_mb": len(buf) / 1e6,
            "note": "measured WITHOUT streaming-state feed-back — internal-states "
            "lite graph keeps states inside only if the delegate supports it; "
            "verify semantics before trusting these numbers",
        }
    return results


def attempt_onnx(model_internal, frames: list[np.ndarray]) -> dict:
    try:
        import onnxruntime as ort
        import tensorflow as tf
        import tf2onnx
    except ImportError as exc:
        return {"error": f"deps missing: {exc}"}
    try:
        spec = (tf.TensorSpec([1, 1, RES, RES, 3], tf.float32, name="image"),)
        proto, _ = tf2onnx.convert.from_keras(model_internal, input_signature=spec)
        buf = proto.SerializeToString()
        sess = ort.InferenceSession(buf, providers=["CPUExecutionProvider"])
        name = sess.get_inputs()[0].name
        outs = []
        t0 = time.perf_counter()
        for fr in frames:
            outs.append(sess.run(None, {name: fr[np.newaxis, np.newaxis].astype(np.float32)})[0])
        wall_ms = (time.perf_counter() - t0) * 1e3 / max(1, len(frames))
        return {
            "per_frame_wall_ms_stateless": wall_ms,
            "size_mb": len(buf) / 1e6,
            "streaming_semantics_ok": False,
            "note": "internal-states graph bakes initial states as constants — "
            "per-call scores do not accumulate evidence; needs explicit-state "
            "re-export before it is usable",
        }
    except Exception as exc:
        return {"error": f"convert/run failed: {exc}"}


def attempt_onnx_states(model_ext, frames: list[np.ndarray]) -> dict:
    """Explicit-state export: flatten the external-states dict into the graph
    signature so the runtime feeds states back per call — correct streaming."""
    try:
        import onnxruntime as ort
        import tensorflow as tf
        import tf2onnx
    except ImportError as exc:
        return {"error": f"deps missing: {exc}"}
    try:
        states = model_ext.init_states([1, 1, RES, RES, 3])
        state_keys = list(states.keys())

        @tf.function
        def step(image, *flat_states):
            states_dict = dict(zip(state_keys, flat_states, strict=True))
            logits, new_states = model_ext({**states_dict, "image": image})
            return (logits, *[new_states[k] for k in state_keys])

        image_spec = tf.TensorSpec([1, 1, RES, RES, 3], tf.float32, name="image")
        state_specs = [
            tf.TensorSpec(states[k].shape, states[k].dtype, name=f"state_{i}")
            for i, k in enumerate(state_keys)
        ]
        proto, _ = tf2onnx.convert.from_function(
            step, input_signature=[image_spec, *state_specs]
        )
        buf = proto.SerializeToString()
        sess = ort.InferenceSession(buf, providers=["CPUExecutionProvider"])
        in_names = [i.name for i in sess.get_inputs()]
        out_names = [o.name for o in sess.get_outputs()]
        image_name = in_names[0]
        n_states = len(in_names) - 1
        ort_dtypes = {
            "tensor(float)": np.float32,
            "tensor(double)": np.float64,
            "tensor(int32)": np.int32,
            "tensor(int64)": np.int64,
            "tensor(bool)": np.bool_,
        }

        def stream(batch: list[np.ndarray]) -> list[np.ndarray]:
            carry = {}
            for inp in sess.get_inputs()[1:]:
                dtype = ort_dtypes[inp.type]
                carry[inp.name] = np.zeros(inp.shape, dtype=dtype)
            outs = []
            for fr in batch:
                feed = {image_name: fr[np.newaxis, np.newaxis].astype(np.float32), **carry}
                res = sess.run(out_names, feed)
                logits_t, new_states = res[0], res[1:]
                outs.append(logits_t.ravel().copy())
                for i, name in enumerate(in_names[1:]):
                    carry[name] = new_states[i]
            return outs

        stream(frames[:5])
        t0 = time.perf_counter()
        outs = stream(frames)
        wall_ms = (time.perf_counter() - t0) * 1e3 / max(1, len(frames))
        half = len(frames) // 2
        a = stream(frames[:half])[-1]
        b = stream(frames[half:] + frames[:half])[-1]
        semantics_ok = not np.allclose(a, b, atol=1e-4)
        return {
            "per_frame_wall_ms": wall_ms,
            "size_mb": len(buf) / 1e6,
            "n_state_tensors": n_states,
            "streaming_semantics_ok": semantics_ok,
            "logits_last": [float(x) for x in outs[-1]],
        }
    except Exception as exc:
        return {"error": f"convert/run failed: {exc}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, default=Path("edge/sample_data/videos/bus1.mp4"))
    parser.add_argument("--ckpt", type=Path, default=Path(CKPT_DEFAULT))
    parser.add_argument("--seconds", type=float, default=25.0)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--out", type=Path, default=Path("edge/runs/movinet-spike.json"))
    args = parser.parse_args(argv)

    import tensorflow as tf

    devices = tf.config.list_physical_devices()
    device_kinds = [d.device_type for d in devices]
    print(f"[env] tf {tf.__version__} | {platform.platform()} | {platform.machine()}")
    print(f"[env] devices: {device_kinds} (no tensorflow-metal in spike venv -> CPU)")

    frames, prep_ms = sample_frames(args.video, args.fps, args.seconds)
    if not frames:
        raise SystemExit(f"no frames decoded from {args.video} (run from repo root)")
    n_frames = len(frames)
    print(
        f"[data] {n_frames} frames @ {args.fps:.0f}fps from {args.video.name}"
        f" | preprocess {prep_ms:.2f} ms/frame"
    )

    model_ext, ckpt_path = build_model(True, args.ckpt)
    tf_res = measure_tf(model_ext, frames)
    probs = np.exp(tf_res["logits_last"]) / np.exp(tf_res["logits_last"]).sum()
    eager_ms = tf_res["per_frame_wall_ms_eager"]
    graph_ms = tf_res["per_frame_wall_ms_graph"]
    print(
        f"[tf] eager {eager_ms:.1f} | graph {graph_ms:.1f} ms/frame"
        f" | sanity P(Fight)={probs[0]:.3f} P(No_Fight)={probs[1]:.3f} (normal footage)"
    )

    model_int, _ = build_model(False, args.ckpt)
    tflite_res = convert_and_measure_tflite(model_int, frames)
    for variant, r in tflite_res.items():
        if "error" in r:
            print(f"[tflite {variant}] FAILED: {r['error'][:120]}...")
        else:
            ms = r["per_frame_wall_ms"]
            print(f"[tflite {variant}] {ms:.1f} ms/frame | {r['size_mb']:.1f} MB (see note)")

    onnx_res = attempt_onnx(model_int, frames)
    if "error" in onnx_res:
        onnx_msg = onnx_res["error"]
    else:
        stateless_ms = onnx_res["per_frame_wall_ms_stateless"]
        onnx_msg = f"{stateless_ms:.1f} ms/frame stateless, semantics broken"
    print(f"[onnx stateless] {onnx_msg}")

    onnx_states_res = attempt_onnx_states(model_ext, frames)
    if "error" in onnx_states_res:
        print(f"[onnx explicit-states] {onnx_states_res['error']}")
    else:
        ox_ms = onnx_states_res["per_frame_wall_ms"]
        ox_mb = onnx_states_res["size_mb"]
        ox_n = onnx_states_res["n_state_tensors"]
        ox_ok = onnx_states_res["streaming_semantics_ok"]
        print(
            f"[onnx explicit-states] {ox_ms:.1f} ms/frame | {ox_mb:.1f} MB"
            f" | {ox_n} state tensors | semantics_ok={ox_ok}"
        )

    report = {
        "date": time.strftime("%Y-%m-%d"),
        "ckpt": str(ckpt_path),
        "variant": "a2_5fps_32bs_0.001lr_0.3dr_0tl",
        "env": {
            "tf": tf.__version__,
            "platform": platform.platform(),
            "devices": device_kinds,
        },
        "protocol": {
            "frames": n_frames,
            "fps": args.fps,
            "seconds": args.seconds,
            "video": str(args.video),
        },
        "preprocess_ms": prep_ms,
        "tf_external_states": tf_res,
        "tflite": tflite_res,
        "onnx": onnx_res,
        "onnx_explicit_states": onnx_states_res,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))
    print(f"[done] -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
