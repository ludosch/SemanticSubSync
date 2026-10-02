"""The static model's table: read from the official float16 ONNX export (protobuf walked by hand)
or from a local float32 safetensors copy, without the onnx library."""
import json

import numpy as np
import pytest

from semantic_subsync import core


def varint(n):
    out = b""
    while True:
        b = n & 0x7F; n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def field(num, payload):           # length-delimited field
    return varint(num << 3 | 2) + varint(len(payload)) + payload


def number(num, value):            # varint field
    return varint(num << 3) + varint(value)


def tensor(name, array, dtype, packed_dims=False):
    dims = field(1, b"".join(varint(d) for d in array.shape)) if packed_dims else b"".join(number(1, d) for d in array.shape)
    return dims + number(2, dtype) + field(8, name.encode()) + field(9, array.tobytes())


def onnx_file(path, table):
    """The layout of the official export: a small initializer at the top, the table in the body
    graph of a Loop node (NodeProto.attribute -> AttributeProto.g)."""
    body = field(5, tensor("embedding.weight", table, 10, packed_dims=True))
    node = field(4, b"Loop") + field(5, field(1, b"body") + number(20, 5) + field(6, body))
    graph = field(5, tensor("small", np.arange(3, dtype=np.float32), 1)) + field(1, node) + field(2, b"g")
    path.write_bytes(number(1, 8) + field(7, graph) + field(8, field(1, b"") + number(2, 17)))


def test_onnx_table_is_found_in_a_sub_graph(tmp_path):
    table = np.arange(24, dtype=np.float16).reshape(4, 6) / 7
    onnx_file(tmp_path / "m.onnx", table)
    got = core._onnx_table(str(tmp_path / "m.onnx"))
    assert got.dtype == np.float16 and got.shape == (4, 6) and (got == table).all()
    assert (core._onnx_table(str(tmp_path / "m.onnx"), "small") == np.arange(3)).all()
    with pytest.raises(ValueError, match="no tensor"):
        core._onnx_table(str(tmp_path / "m.onnx"), "missing")


def test_safetensors_table(tmp_path):
    table = np.arange(12, dtype=np.float32).reshape(3, 4)
    head = json.dumps({"embedding.weight": {"dtype": "F32", "shape": [3, 4], "data_offsets": [0, 48]}}).encode()
    (tmp_path / "m.safetensors").write_bytes(len(head).to_bytes(8, "little") + head + table.tobytes())
    assert (core._safetensors_table(str(tmp_path / "m.safetensors")) == table).all()


def test_local_copy_prefers_the_fp16_export_and_keeps_the_first_dims(tmp_path):
    tokenizers = pytest.importorskip("tokenizers")
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    tok = tokenizers.Tokenizer(WordLevel({"[UNK]": 0, "hello": 1, "world": 2, "keys": 3}, unk_token="[UNK]"))
    tok.pre_tokenizer = Whitespace()
    tok.save(str(tmp_path / "tokenizer.json"))
    table = (np.arange(24, dtype=np.float32).reshape(4, 6) / 7).astype(np.float16)
    onnx_file(tmp_path / "model_fp16.onnx", table)
    emb = core._load_static(dict(core.MODELS["static"], dims=4), str(tmp_path))
    v = emb(["hello world", "keys", ""])
    assert v.dtype == np.float32 and v.shape == (3, 4)
    assert np.allclose(v[0], table[[1, 2], :4].astype(np.float32).mean(0)) and (v[2] == 0).all()


def test_download_checks_the_sha256_and_keeps_only_verified_files(tmp_path, monkeypatch):
    import hashlib, io, os, urllib.request
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    body, urls = b"table bytes", []
    def urlopen(url, timeout):
        urls.append(url); return io.BytesIO(body)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    m = dict(repo="org/model", revision="abc", sha256={"onnx/t.onnx": hashlib.sha256(body).hexdigest()})
    path = core._download(m, "onnx/t.onnx")
    assert open(path, "rb").read() == body and path.startswith(str(tmp_path / "semantic-subsync" / "org--model" / "abc"))
    assert urls == ["https://huggingface.co/org/model/resolve/abc/onnx/t.onnx"]
    assert core._download(m, "onnx/t.onnx") == path and len(urls) == 1          # kept: no second download
    m["sha256"]["onnx/u.onnx"] = "0" * 64
    with pytest.raises(ValueError, match="SHA-256"):
        core._download(m, "onnx/u.onnx")
    assert os.listdir(os.path.dirname(path)) == ["t.onnx"]                       # nothing left of the bad one
