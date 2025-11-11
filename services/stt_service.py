import os
import importlib
import importlib.util
from typing import Optional, List, Dict, Any


class STTService:
    """
    语音转文字服务，支持 Whisper 与 Fun-ASR，支持模型预下载与离线运行。

    后端选择规则：
    - 自动（auto）：优先使用已安装的 whisper，其次 fun-asr。
    - 指定（whisper|funasr）：强制使用对应后端。

    依赖：
    - Whisper：`pip install whisper`（OpenAI whisper），需要系统安装 ffmpeg。
    - Fun-ASR：`pip install funasr modelscope`，需要联网预下载模型或离线缓存。
    """

    def __init__(self):
        self.available_backends: List[str] = []
        self._discover_backends()

    def _discover_backends(self):
        self.available_backends = []
        try:
            # 使用 importlib 查找可选依赖，避免静态导入引发诊断错误
            if importlib.util.find_spec('whisper') is not None:
                self.available_backends.append('whisper')
        except Exception:
            pass
        try:
            # Fun-ASR 依赖 ModelScope
            if importlib.util.find_spec('modelscope') is not None:
                self.available_backends.append('funasr')
        except Exception:
            pass

    def available(self) -> List[str]:
        return list(self.available_backends)

    def get_best_backend(self) -> Optional[str]:
        return self.available_backends[0] if self.available_backends else None

    def prepare_model(
        self,
        backend: str = 'whisper',
        model: Optional[str] = None,
        cache_dir: Optional[str] = None,
        language: Optional[str] = None,
    ) -> bool:
        """
        预下载模型以便离线运行：
        - whisper：通过 load_model 触发下载；支持 `download_root`。
        - funasr：通过 modelscope 的 snapshot_download 或 pipeline 首次加载触发缓存。
        返回是否成功。
        """
        backend = (backend or 'whisper').lower()
        cache_dir = cache_dir or os.path.expanduser('~/.cache/d2_models')
        os.makedirs(cache_dir, exist_ok=True)
        try:
            if backend == 'whisper':
                # 动态导入 whisper，避免未安装时的解析报错
                try:
                    whisper_mod = importlib.import_module('whisper')
                except Exception:
                    return False
                model_name = model or 'base'
                # OpenAI whisper 支持 download_root 指定缓存目录
                whisper_mod.load_model(model_name, download_root=cache_dir)
                return True
            elif backend == 'funasr':
                # 选择一个常用中文/英文模型 ID（可覆盖）
                model_id = model or (
                    'iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'
                    if (language or 'zh').lower().startswith('zh')
                    else 'damo/speech_paraformer-large-vad-punc-asr-en'
                )

                # 优先尝试通过 snapshot_download 下载模型到缓存
                snapshot_ok = False
                try:
                    hub_mod = importlib.import_module('modelscope.hub.snapshot_download')
                    snapshot_download = getattr(hub_mod, 'snapshot_download', None)
                    if snapshot_download is not None:
                        snapshot_download(model_id, cache_dir=cache_dir)
                        snapshot_ok = True
                except Exception:
                    pass
                if snapshot_ok:
                    return True

                # 退路：通过 pipeline 首次加载触发缓存
                try:
                    pipelines_mod = importlib.import_module('modelscope.pipelines')
                    const_mod = importlib.import_module('modelscope.utils.constant')
                    pipeline = getattr(pipelines_mod, 'pipeline')
                    Tasks = getattr(const_mod, 'Tasks')
                    task = getattr(Tasks, 'auto_speech_recognition')
                    _ = pipeline(task=task, model=model_id)
                    return True
                except Exception:
                    return False
        except Exception:
            return False
        return False

    def transcribe(
        self,
        audio_path: str,
        backend: Optional[str] = None,
        model: Optional[str] = None,
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行转写并返回：{"ok": bool, "text": str, "backend": str, "error": Optional[str]}
        - audio_path: 本地音频文件路径（支持 whisper 常见格式，funasr 推荐 wav/pcm）。
        - backend: 'whisper'/'funasr'/'auto'（默认自动）。
        - model: 后端模型名或模型ID。
        - language: 语言提示（whisper: 'zh'/'en'，funasr用于挑选默认模型）。
        - prompt: 初始提示（仅 whisper 支持 initial_prompt）。
        """
        if not isinstance(audio_path, str) or not os.path.isfile(audio_path):
            return {"ok": False, "error": "音频路径不存在或不可读。", "text": "", "backend": backend or 'auto'}

        b = (backend or 'auto').lower()
        if b == 'auto':
            b = self.get_best_backend() or 'whisper'

        try:
            if b == 'whisper':
                try:
                    whisper_mod = importlib.import_module('whisper')
                except Exception:
                    return {"ok": False, "error": "whisper 未安装，请执行: pip install whisper，并确保系统安装 ffmpeg。", "text": "", "backend": 'whisper'}
                model_name = model or 'base'
                w = whisper_mod.load_model(model_name)
                args: Dict[str, Any] = {}
                if language and isinstance(language, str) and language.strip():
                    args['language'] = language
                if prompt and isinstance(prompt, str) and prompt.strip():
                    args['initial_prompt'] = prompt
                try:
                    res = w.transcribe(audio_path, **args)
                except Exception as e:
                    return {"ok": False, "error": f"whisper 转写失败: {e}", "text": "", "backend": 'whisper'}
                text = str(res.get('text') or '').strip()
                return {"ok": True, "text": text, "backend": 'whisper'}

            elif b == 'funasr':
                try:
                    pipelines_mod = importlib.import_module('modelscope.pipelines')
                    const_mod = importlib.import_module('modelscope.utils.constant')
                    pipeline = getattr(pipelines_mod, 'pipeline')
                    Tasks = getattr(const_mod, 'Tasks')
                except Exception:
                    return {"ok": False, "error": "fun-asr 未安装，请执行: pip install funasr modelscope。", "text": "", "backend": 'funasr'}
                model_id = model or (
                    'iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch'
                    if (language or 'zh').lower().startswith('zh')
                    else 'damo/speech_paraformer-large-vad-punc-asr-en'
                )
                try:
                    infer = pipeline(task=getattr(Tasks, 'auto_speech_recognition'), model=model_id)
                    out = infer(audio_in=audio_path)
                except Exception as e:
                    return {"ok": False, "error": f"fun-asr 转写失败: {e}", "text": "", "backend": 'funasr'}
                # ModelScope 常见输出包含 'text'，不同模型可能返回字典结构
                if isinstance(out, dict):
                    text = str(out.get('text') or out.get('raw_text') or '').strip()
                else:
                    text = str(out or '').strip()
                return {"ok": True, "text": text, "backend": 'funasr'}
            else:
                return {"ok": False, "error": f"不支持的后端: {b}", "text": "", "backend": b}
        except Exception as e:
            return {"ok": False, "error": f"转写过程异常: {e}", "text": "", "backend": b}


if __name__ == "__main__":
    svc = STTService()
    print(f"可用后端: {svc.available()}")
    # 示例：准备离线模型（如安装了相应库）
    # svc.prepare_model('whisper', 'base')
    # svc.prepare_model('funasr', None, language='zh')