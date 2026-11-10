"""Transient DNS result for one durable model step, never checkpointed."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from app.errors import AppError
from app.execution.effects import EffectContext, get_effect_context


@dataclass(frozen=True)
class ModelAddress:
    key: tuple
    ip: str


@dataclass
class ModelNetwork:
    effect: EffectContext
    address: ModelAddress
    attempts: int = 0


_network: ContextVar[ModelNetwork | None] = ContextVar("step_model_network", default=None)


def address_key(config):
    return (config.model_config_id, config.config_version, config.base_url, config.allow_private_network)


async def prepare_model_address(execution, *, config=None, model_config_id=None, model_config_version=None):
    if get_effect_context() is not None:
        raise AppError("EXECUTION_PREFLIGHT_ONLY", "模型地址只能在签发前准备", 409)
    if config is None:
        from app.agents.model_adapter import load_config
        config = load_config(model_config_id, expected_version=model_config_version,
                             context=execution.context, session=execution.session)
    from app.model_catalog.security import approved_model_address
    address = await approved_model_address(config)
    execution.model_address = ModelAddress(address_key(config), address)


@contextmanager
def bind_model_network(execution, effect):
    address = execution.model_address
    token = _network.set(ModelNetwork(effect, address) if address is not None else None)
    try:
        yield
    finally:
        _network.reset(token)


def begin_model_transport(config):
    network = _network.get()
    if network is None:
        return None, False
    if network.effect != get_effect_context() or network.address.key != address_key(config):
        raise AppError("MODEL_VERSION_CHANGED", "模型地址与当前已核对的执行配置不符", 409)
    network.attempts += 1
    return network.address.ip, network.attempts == 1


_CONNECTION_SEAL = object()


class ModelConnectionFailure(AppError):
    def __init__(self, seal, effect, phase):
        if seal is not _CONNECTION_SEAL:
            raise TypeError("Model connection failures require trusted transport evidence")
        self._seal = seal
        self._effect = effect
        super().__init__("MODEL_UNREACHABLE", "模型服务暂时无法连接，请核对服务地址和网络后新建运行", 502,
                         {"phase": phase, "effect_issued": True, "request_sent": False,
                          "response_received": False, "automatic_retry": False})


class ConnectionTrace:
    """Only a first-request TCP/TLS failure before HTTP headers proves non-delivery."""

    def __init__(self):
        self.effect = get_effect_context()
        self.phase = None
        self.sent = False

    async def observe(self, event, _info):
        if event in {"http11.send_request_headers.started", "http2.send_request_headers.started"}:
            self.sent = True
        elif event in {"connection.connect_tcp.failed", "connection.start_tls.failed"}:
            self.phase = "tcp_connect" if event == "connection.connect_tcp.failed" else "tls_handshake"

    def error(self):
        if self.effect is not None and self.effect == get_effect_context() and self.phase and not self.sent:
            return ModelConnectionFailure(_CONNECTION_SEAL, self.effect, self.phase)
        return None


def is_proven_model_connection_failure(error, effect):
    return (effect is not None and type(error) is ModelConnectionFailure
            and error._seal is _CONNECTION_SEAL and error._effect == effect)
