"""Provider identities and official login profiles shared by MCP and workers."""
from __future__ import annotations

from typing import Literal

Provider = Literal["ctrip", "qunar", "fliggy", "tongcheng", "tuniu"]
LoginProvider = Literal["ctrip", "qunar", "fliggy", "fliggy_h5", "tongcheng", "tuniu"]
PROVIDERS = ("ctrip", "qunar", "fliggy", "tongcheng", "tuniu")
NAMES = {"ctrip": "携程", "qunar": "去哪儿", "fliggy": "飞猪", "tongcheng": "同程", "tuniu": "途牛"}
LOGIN_URLS = {"ctrip": "https://www.ctrip.com/", "qunar": "https://user.qunar.com/",
    "fliggy": "https://www.fliggy.com/", "fliggy_h5": "https://market.m.taobao.com/app/trip/rx-traffic-search/pages/index?biz=flight&defaultBiz=flight",
    "tongcheng": "https://www.ly.com/", "tuniu": "https://www.tuniu.com/"}


def validate_provider(value: str, *, login: bool = False) -> str:
    if value not in (LOGIN_URLS if login else PROVIDERS):
        raise ValueError("未知平台，请使用 ctrip/qunar/fliggy/tongcheng/tuniu" + ("，飞猪 H5 登录另用 fliggy_h5" if login else ""))
    return value


def metadata_key(key: str, provider: str) -> str:
    return key if provider == "ctrip" else f"{key}:{provider}"
