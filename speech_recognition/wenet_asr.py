"""WenetSpeech U2++ Conformer：本地检查点加载及 CTC 贪心识别。"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import torch
import yaml

from .audio import compute_fbank, load_wav, pad_sequence, resample


DEFAULT_MODEL_DIR = Path(__file__).resolve().parent / "models" / "wenet"


def load_vocab(path: str | Path) -> dict[int, str]:
    """读取 units.txt 的 token/id 映射，并拒绝重复或不连续的编号。"""
    vocab: dict[int, str] = {}
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                token, index = line.strip().rsplit(maxsplit=1)
                token_id = int(index)
            except ValueError as exc:
                raise ValueError(f"词表第 {line_no} 行格式错误") from exc
            if token_id < 0 or token_id in vocab or token in seen:
                raise ValueError(f"词表第 {line_no} 行存在重复或非法编号")
            vocab[token_id] = token
            seen.add(token)
    if not vocab or set(vocab) != set(range(len(vocab))):
        raise ValueError("词表编号必须从 0 开始连续排列")
    return vocab


def select_device(name: str) -> torch.device:
    """自动选择 CUDA、MPS、CPU；显式请求不可用设备时直接报错。"""
    if name not in {"auto", "cpu", "mps", "cuda"}:
        raise ValueError(f"不支持的设备：{name}")
    if name == "auto":
        if torch.cuda.is_available():
            name = "cuda"
        elif torch.backends.mps.is_available():
            name = "mps"
        else:
            name = "cpu"
    if name == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA 不可用，请检查 NVIDIA 驱动及 PyTorch 安装版本")
    if name == "mps" and not torch.backends.mps.is_available():
        raise RuntimeError("MPS 不可用，请检查 macOS/PyTorch 环境，或选择 --device cpu")
    return torch.device(name)


class WeNetASR:
    """加载一次模型，多次调用 transcribe(wav_file) 得到字符串。"""

    def __init__(self, model_dir: str | Path = DEFAULT_MODEL_DIR, device: str = "auto"):
        self.model_dir = Path(model_dir).expanduser().resolve()
        for name in ("final.pt", "train.yaml", "units.txt", "global_cmvn"):
            if not (self.model_dir / name).is_file():
                raise FileNotFoundError(f"缺少模型资源：{self.model_dir / name}")
        try:
            with (self.model_dir / "train.yaml").open(encoding="utf-8") as stream:
                self.config = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise ValueError("train.yaml 格式错误") from exc
        if not isinstance(self.config, dict):
            raise ValueError("train.yaml 须为配置字典")
        required = {"input_dim", "output_dim", "encoder_conf", "decoder_conf",
                    "model_conf", "dataset_conf", "tokenizer_conf", "cmvn_conf"}
        missing = required - self.config.keys()
        if missing:
            raise ValueError(f"模型配置缺少字段：{', '.join(sorted(missing))}")
        supported = {"encoder": "conformer", "decoder": "bitransformer",
                     "model": "asr_model", "tokenizer": "char", "cmvn": "global_cmvn"}
        for key, expected in supported.items():
            if self.config.get(key) != expected:
                raise ValueError(f"本模块要求 {key}={expected}，请使用配套 WenetSpeech 模型")
        if self.config["encoder_conf"].get("input_layer") != "conv2d":
            raise ValueError("本模块当前要求 conv2d 四倍下采样模型")

        # 只修改内存中的配置，避免依赖发布包里的 exp/... 或当前工作目录。
        self.config["tokenizer_conf"]["symbol_table_path"] = str(self.model_dir / "units.txt")
        self.config["cmvn_conf"]["cmvn_file"] = str(self.model_dir / "global_cmvn")
        self.vocab = load_vocab(self.model_dir / "units.txt")
        if len(self.vocab) != self.config["output_dim"]:
            raise ValueError("词表大小与模型 output_dim 不一致")
        self.blank_id = self.config.get("ctc_conf", {}).get("ctc_blank_id", 0)
        if self.vocab.get(self.blank_id) != "<blank>":
            raise ValueError("CTC blank 编号与词表不一致")
        self.special_ids = {
            key for key, value in self.vocab.items()
            if value in {"<blank>", "<sos>", "<eos>", "<sos/eos>"}
        }
        dataset = self.config["dataset_conf"]
        self.sample_rate = int(dataset["resample_conf"]["resample_rate"])
        fbank = dataset["fbank_conf"]
        self.feature_options = {
            "num_mel_bins": int(fbank["num_mel_bins"]),
            "frame_length": float(fbank["frame_length"]),
            "frame_shift": float(fbank["frame_shift"]),
            "window_type": fbank.get("window_type", "povey"),
        }
        if self.feature_options["num_mel_bins"] != self.config["input_dim"]:
            raise ValueError("Fbank 维数与模型 input_dim 不一致")
        self.device = select_device(device)
        self.model = self._load_model().float().eval().to(self.device)

    def _load_model(self) -> torch.nn.Module:
        # 固定 WeNet v3.1.0 的模块路径；直接构造官方网络，不调用训练入口。
        from wenet.transformer.asr_model import ASRModel
        from wenet.transformer.cmvn import GlobalCMVN
        from wenet.transformer.ctc import CTC
        from wenet.transformer.decoder import BiTransformerDecoder
        from wenet.transformer.embedding import PositionalEncoding, RelPositionalEncoding
        from wenet.transformer.encoder import ConformerEncoder
        from wenet.utils.cmvn import load_cmvn

        cfg = self.config
        mean, inverse_std = load_cmvn(
            cfg["cmvn_conf"]["cmvn_file"], cfg["cmvn_conf"]["is_json_cmvn"]
        )
        cmvn = GlobalCMVN(torch.as_tensor(mean).float(), torch.as_tensor(inverse_std).float())
        encoder = ConformerEncoder(cfg["input_dim"], global_cmvn=cmvn, **cfg["encoder_conf"])
        decoder = BiTransformerDecoder(
            cfg["output_dim"], encoder.output_size(), **cfg["decoder_conf"]
        )
        model = ASRModel(
            vocab_size=cfg["output_dim"], encoder=encoder, decoder=decoder,
            ctc=CTC(cfg["output_dim"], encoder.output_size(), blank_id=self.blank_id),
            special_tokens=cfg["tokenizer_conf"].get("special_tokens"),
            **cfg["model_conf"],
        )
        state = torch.load(self.model_dir / "final.pt", map_location="cpu", weights_only=True)
        if not isinstance(state, Mapping):
            raise ValueError("final.pt 不是预期的 PyTorch state_dict")
        # 2022 年检查点未保存固定的正弦位置编码；v3.1.0 将其注册为持久 buffer。
        # 仅补齐这三个可由公式重建的缓存，其他参数仍严格检查。
        state = state.copy()
        for module_path in (
            "encoder.embed.pos_enc",
            "decoder.left_decoder.embed.1",
            "decoder.right_decoder.embed.1",
        ):
            key = f"{module_path}.pe"
            if key in state:
                continue
            module = model.get_submodule(module_path)
            # 精确限定类型，不能把可学习位置编码的随机初值当作已训练权重。
            if type(module) not in (PositionalEncoding, RelPositionalEncoding):
                raise ValueError(f"无法重建位置编码 {key}：不是固定正弦位置编码")
            buffers = dict(module.named_buffers(recurse=False))
            if "pe" not in buffers:
                raise ValueError(f"无法重建位置编码 {key}：缺少 pe 缓存")
            state[key] = buffers["pe"]
        model.load_state_dict(state, strict=True)
        return model

    def preprocess(self, wav_file: str | Path) -> tuple[torch.Tensor, torch.Tensor]:
        """在 CPU 提取特征；CMVN 由模型编码器执行一次。"""
        waveform, source_rate = load_wav(wav_file)
        waveform = resample(waveform, source_rate, self.sample_rate)
        features = compute_fbank(waveform, self.sample_rate, **self.feature_options)
        # 两层 kernel=3、stride=2 的卷积至少需要 7 帧。
        if features.size(0) < 7:
            raise ValueError("音频太短，至少需要 7 帧特征（当前配置约 85 ms）")
        if not torch.isfinite(features).all():
            raise ValueError("音频特征包含非有限值")
        return pad_sequence([features])

    def map_batch(self, sequences: Sequence[Sequence[int]]) -> list[str]:
        """将已经完成 CTC 合并的编号转为文字，保留未知词标记。"""
        texts = []
        for sequence in sequences:
            tokens = [self.vocab[index] for index in sequence if index not in self.special_ids]
            texts.append("".join(tokens).replace("▁", " ").strip())
        return texts

    def post_process(self, frame_ids: Sequence[int]) -> str:
        """先合并相邻重复编号，再去 blank；blank 隔开的重复字必须保留。"""
        merged = []
        previous = None
        for token_id in frame_ids:
            if token_id != previous and token_id != self.blank_id:
                merged.append(token_id)
            previous = token_id
        return self.map_batch([merged])[0]

    @torch.inference_mode()
    def transcribe(self, wav_file: str | Path) -> str:
        """离线识别整个 WAV；不截断、不录音，不触发机械臂操作。"""
        features, lengths = self.preprocess(wav_file)
        encoded, mask = self.model.encoder(
            features.to(self.device), lengths.to(self.device), decoding_chunk_size=-1
        )
        probabilities = self.model.ctc.log_softmax(encoded)
        if not torch.isfinite(probabilities).all():
            raise RuntimeError("模型输出包含非有限值，请检查模型资源及计算设备")
        valid_length = int(mask.squeeze(1).sum(dim=1)[0].item())
        frame_ids = probabilities.argmax(dim=-1)[0, :valid_length].cpu().tolist()
        return self.post_process(frame_ids)
