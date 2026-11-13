"""Clients for the CorpOne network-provisioning and billing APIs, routed through the outbound gateway.

The HTTP paths/payloads below are a placeholder contract. Adjust them to match the real
network (HLR/HSS/SM-DP+ orchestration) and billing (BSS) API specifications.
"""
import uuid
from typing import Protocol

import httpx

from app.config import get_settings
from app.gateway.outbound import GatewayError, gateway, http_post


class TelcoApiError(RuntimeError):
    pass


class NetworkApi(Protocol):
    def activate_line(self, *, iccid: str, msisdn: str, sim_type: str, plan_code: str,
                      idempotency_key: str) -> str: ...


class BillingApi(Protocol):
    def create_account(self, *, account_number: str, company: dict, account_officer: dict,
                       merchant_code: str, idempotency_key: str) -> str: ...

    def add_subscription(self, *, billing_account_ref: str, msisdn: str, iccid: str,
                         plan_code: str, monthly_price: str, idempotency_key: str) -> str: ...


# --------------------------------------------------------------------------- raw implementations

class MockNetworkApi:
    def activate_line(self, *, iccid, msisdn, sim_type, plan_code, idempotency_key):
        return f"NET-{uuid.uuid5(uuid.NAMESPACE_OID, idempotency_key).hex[:12].upper()}"


class MockBillingApi:
    def create_account(self, *, account_number, company, account_officer, merchant_code, idempotency_key):
        return f"BA-{uuid.uuid5(uuid.NAMESPACE_OID, idempotency_key).hex[:12].upper()}"

    def add_subscription(self, *, billing_account_ref, msisdn, iccid, plan_code, monthly_price, idempotency_key):
        return f"SUB-{uuid.uuid5(uuid.NAMESPACE_OID, idempotency_key).hex[:12].upper()}"


def _http_client(base_url: str) -> httpx.Client:
    s = get_settings()
    return httpx.Client(base_url=base_url, timeout=s.telco_api_timeout,
                        headers={"Authorization": f"Bearer {s.telco_api_key}"} if s.telco_api_key else {})


def _ref(body: dict, field: str, path: str) -> str:
    if field not in body:
        raise GatewayError(f"POST {path}: response missing '{field}'")
    return str(body[field])


class HttpNetworkApi:
    def __init__(self):
        self._client = _http_client(get_settings().network_api_base_url)

    def activate_line(self, *, iccid, msisdn, sim_type, plan_code, idempotency_key):
        path = "/v1/subscribers"
        body = http_post(self._client, path, {"iccid": iccid, "msisdn": msisdn, "simType": sim_type,
                                              "planCode": plan_code}, idempotency_key)
        return _ref(body, "subscriberId", path)


class HttpBillingApi:
    def __init__(self):
        self._client = _http_client(get_settings().billing_api_base_url)

    def create_account(self, *, account_number, company, account_officer, merchant_code, idempotency_key):
        path = "/v1/corporate-accounts"
        body = http_post(self._client, path, {"accountNumber": account_number, "company": company,
                                              "accountOfficer": account_officer, "merchantCode": merchant_code},
                         idempotency_key)
        return _ref(body, "billingAccountId", path)

    def add_subscription(self, *, billing_account_ref, msisdn, iccid, plan_code, monthly_price, idempotency_key):
        path = f"/v1/corporate-accounts/{billing_account_ref}/subscriptions"
        body = http_post(self._client, path, {"msisdn": msisdn, "iccid": iccid, "planCode": plan_code,
                                              "monthlyPrice": monthly_price}, idempotency_key)
        return _ref(body, "subscriptionId", path)


# --------------------------------------------------------------------------- gateway-routed wrappers

def _via_gateway(upstream: str, operation: str, fn):
    try:
        return gateway.call(upstream, operation, fn)
    except GatewayError as exc:
        raise TelcoApiError(str(exc)) from exc


class GatewayNetworkApi:
    def __init__(self, inner: NetworkApi):
        self.inner = inner

    def activate_line(self, **kw):
        return _via_gateway("network", "activate_line", lambda: self.inner.activate_line(**kw))


class GatewayBillingApi:
    def __init__(self, inner: BillingApi):
        self.inner = inner

    def create_account(self, **kw):
        return _via_gateway("billing", "create_account", lambda: self.inner.create_account(**kw))

    def add_subscription(self, **kw):
        return _via_gateway("billing", "add_subscription", lambda: self.inner.add_subscription(**kw))


def get_network_api() -> NetworkApi:
    http = get_settings().telco_api_mode == "http"
    return GatewayNetworkApi(HttpNetworkApi() if http else MockNetworkApi())


def get_billing_api() -> BillingApi:
    http = get_settings().telco_api_mode == "http"
    return GatewayBillingApi(HttpBillingApi() if http else MockBillingApi())
