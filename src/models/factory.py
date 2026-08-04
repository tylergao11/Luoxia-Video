from ..utils import get_logger

logger = get_logger(__name__)

class ModelFactory:
    @staticmethod
    def create_model(config):
        model_cfg = config.get('model') if isinstance(config.get('model'), dict) else {}
        model_name = config.get('model.name') or model_cfg.get('name')
        if model_name == 'wanx':
            from .wanx import WanxModel
            return WanxModel(model_cfg or config.get('model'))
        elif model_name in ('kling', 'kling-v3'):
            from .kling import KlingModel
            return KlingModel(model_cfg or {})
        elif model_name in ('vidu', 'viduq3-pro', 'viduq3-turbo'):
            from .vidu import ViduModel
            return ViduModel(model_cfg or {})
        elif model_name in ('seedance', 'seedance-2.0'):
            from .mulerouter import MuleRouterVideoModel
            return MuleRouterVideoModel(model_cfg or {})
        elif isinstance(model_name, str) and model_name.startswith('grok-imagine-video'):
            from .grok import GrokVideoModel
            return GrokVideoModel(model_cfg or {})
        else:
            raise ValueError(f"Unknown model: {model_name}")
