"""Build backend/assets/models/yamnet.onnx from Google's official YAMNet weights (CI only).

Sources (Apache 2.0):
  code     https://github.com/tensorflow/models/tree/master/research/audioset/yamnet
  weights  https://storage.googleapis.com/audioset/yamnet.h5
The ONNX model takes log-mel patches [N, 96, 64]; ClipFarm computes the patches in numpy
(clipfarm/highlights/audio_events.py). This script checks that, for the same audio, ClipFarm's numpy
features + the ONNX model give the same class scores as the official TensorFlow waveform model.

Usage: python scripts/build_yamnet.py <tensorflow-models/research/audioset/yamnet dir> <yamnet.h5> <out dir>
Needs tensorflow-cpu 2.15 + tf2onnx + onnxruntime (installed only in the CI step that runs this).
"""
import shutil
import sys
from pathlib import Path

import numpy as np

yam_dir, h5, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path.insert(0, str(yam_dir))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import tensorflow as tf  # noqa: E402
import tf2onnx  # noqa: E402
import tf_keras  # noqa: E402  (the YAMNet code builds with tf_keras)
import onnxruntime as ort  # noqa: E402

import params as yparams  # noqa: E402
import yamnet as ymodel  # noqa: E402

p = yparams.Params()
# Official waveform model (reference).
ref = ymodel.yamnet_frames_model(p)
ref.load_weights(str(h5))

# Patches-in model with the same weights.
inp = tf_keras.layers.Input(shape=(96, 64), name="patches")
preds, _emb = ymodel.yamnet(inp, p)
patch_model = tf_keras.Model(inp, preds)
patch_model.set_weights(ref.get_weights())

out.mkdir(parents=True, exist_ok=True)
spec = (tf.TensorSpec((None, 96, 64), tf.float32, name="patches"),)
try:
    tf2onnx.convert.from_keras(patch_model, input_signature=spec, opset=17, output_path=str(out / "yamnet.onnx"))
except Exception as e:      # tf_keras models sometimes need the plain-function route
    print("from_keras failed, converting as a tf.function:", e)
    fn = tf.function(lambda x: patch_model(x, training=False), input_signature=spec)
    tf2onnx.convert.from_function(fn, input_signature=spec, opset=17, output_path=str(out / "yamnet.onnx"))
shutil.copy2(yam_dir / "yamnet_class_map.csv", out / "yamnet_class_map.csv")

# ---- check: official TF (waveform) vs ClipFarm numpy features + ONNX ----
from clipfarm.highlights import audio_events as A  # noqa: E402

rng = np.random.default_rng(0)
t = np.arange(16000 * 6) / 16000
wave = (0.2 * np.sin(2 * np.pi * 440 * t) * (t % 1 < 0.5) + 0.05 * rng.standard_normal(t.size)).astype(np.float32)
tf_scores, _, tf_logmel = ref(wave)
tf_scores = tf_scores.numpy()
sess = ort.InferenceSession(str(out / "yamnet.onnx"), providers=["CPUExecutionProvider"])
mine = sess.run(None, {sess.get_inputs()[0].name: A.patches(wave)})[0]
feat_diff = float(np.abs(A.log_mel(A.pad_waveform(wave)) - tf_logmel.numpy()).max())
score_diff = float(np.abs(mine - tf_scores).max())
print(f"patches: tf={tf_scores.shape} onnx={mine.shape}  max feature diff={feat_diff:.5f}  max score diff={score_diff:.5f}")
assert mine.shape == tf_scores.shape, "patch count differs"
assert feat_diff < 1e-2 and score_diff < 2e-3, "ClipFarm features/ONNX don't match official YAMNet"
(out / "yamnet_check.txt").write_text(
    f"Checked against official TensorFlow YAMNet: max log-mel diff {feat_diff:.6f}, max score diff {score_diff:.6f}\n")
(out / "LICENSE-yamnet.txt").write_text(
    "YAMNet model and weights: Copyright Google LLC, Apache License 2.0\n"
    "https://github.com/tensorflow/models/tree/master/research/audioset/yamnet\n"
    "Class names: AudioSet ontology, Google, CC BY-SA 4.0 (https://research.google.com/audioset/)\n")
print("ok")
