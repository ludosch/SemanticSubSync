"""Make the int8 copy of the minilm model for small CPUs (112 MB, ~40 % faster, ~2.5x less RAM).

fastembed ships the model as FP16 ONNX, which a CPU runs as FP32 anyway. This script converts it
to FP32, then applies dynamic int8 quantization to the MatMul, Attention and Gather (embedding
table) operators. Write it to <models>/minilm and point SEMSYNC_MODEL_DIR at <models>.

Usage: uv run --extra model --with onnx python tools/quantize_model.py MODELS_DIR/minilm
"""
import os, shutil, sys, tempfile

import numpy as np
import onnx
from onnx import TensorProto, numpy_helper
from onnxruntime.quantization import QuantType, quantize_dynamic

from semantic_subsync.core import MODELS

ONNX = "model_optimized.onnx"


def to_fp32(model):
    g = model.graph
    for i, t in enumerate(g.initializer):
        if t.data_type == TensorProto.FLOAT16:
            g.initializer[i].CopyFrom(numpy_helper.from_array(numpy_helper.to_array(t).astype(np.float32), t.name))
    for vi in list(g.input) + list(g.output) + list(g.value_info):
        if vi.type.tensor_type.elem_type == TensorProto.FLOAT16:
            vi.type.tensor_type.elem_type = TensorProto.FLOAT
    for n in g.node:
        for a in n.attribute:
            if a.name == "to" and a.i == TensorProto.FLOAT16:
                a.i = TensorProto.FLOAT
            if a.type == onnx.AttributeProto.TENSOR and a.t.data_type == TensorProto.FLOAT16:
                a.t.CopyFrom(numpy_helper.from_array(numpy_helper.to_array(a.t).astype(np.float32), a.t.name))
    return model


def source_files():
    """The folder of the model files fastembed uses for minilm, and the name of its ONNX file.
    fastembed's public model list gives the Hugging Face repository it downloads from;
    huggingface_hub downloads it (or finds it in its cache)."""
    from fastembed import TextEmbedding
    from huggingface_hub import snapshot_download
    name = MODELS["minilm"]["repo"]
    desc = next((m for m in TextEmbedding.list_supported_models() if m["model"] == name), None)
    repo = desc and (desc.get("sources") or {}).get("hf")
    if not repo:
        sys.exit(f"fastembed no longer lists a Hugging Face source for {name}: "
                 f"this script needs updating for the installed fastembed version")
    return snapshot_download(repo), desc.get("model_file") or ONNX


def main(out):
    src, onnx_file = source_files()
    if onnx_file != ONNX:
        sys.exit(f"fastembed's minilm file is {onnx_file}, expected {ONNX}: update this script")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(src):                      # tokenizer and config files, as is
        if f != ONNX and os.path.isfile(os.path.join(src, f)):
            shutil.copy(os.path.realpath(os.path.join(src, f)), os.path.join(out, f))
    with tempfile.TemporaryDirectory() as tmp:
        fp32 = os.path.join(tmp, ONNX)
        onnx.save(to_fp32(onnx.load(os.path.realpath(os.path.join(src, ONNX)))), fp32)
        quantize_dynamic(fp32, os.path.join(out, ONNX), weight_type=QuantType.QInt8,
                         op_types_to_quantize=["MatMul", "Attention", "Gather"])
    print(f"{os.path.join(out, ONNX)}: {os.path.getsize(os.path.join(out, ONNX)) // 2**20} MB")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
